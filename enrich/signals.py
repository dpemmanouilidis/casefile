"""Deterministic signals computed from a Case (see enrich/case.py). Every
signal is a pure function: Case in, signal dict out. No I/O, no network —
same discipline gate/ needs, one layer earlier, since a signal is exactly
the kind of thing a gate rule consumes without re-deriving.

Every signal returns a value AND a completeness marker. A signal computed
over a trace containing not_ingested or fan_out_cap nodes is incomplete,
and says so explicitly rather than silently reporting "no exposure found"
when the honest answer is "exposure not measurable" (rule 3, fail closed).

Thresholds and windows used here are named constants with the reasoning
in a comment, per the milestone-3 design constraint: every one of them
will be questioned by a reader, and none should require reading the code
to find. (Also mirrored in README.md.)
"""

from __future__ import annotations

GAP_TERMINAL_REASONS = {"not_ingested", "fan_out_cap"}
CUSTODY_CATEGORIES = {"exchange", "mixer", "bridge"}

# pass_through: how close in block-time a received amount and an outbound
# amount have to be to count as "forwarded", and how close in value.
PASS_THROUGH_WINDOW_BLOCKS = 100  # ~20 minutes at ~12s/block: catches same-session
# forwarding without treating unrelated later reuse of an address as pass-through.
PASS_THROUGH_TOLERANCE_PCT = 0.05  # forwarded amount within 5% of received: allows
# for the outbound leg being shaved by gas fees without loosening enough to catch
# genuinely unrelated transfers of a similar size.


def _categories_of(case: dict, address: str) -> set[str]:
    return {row["category"] for row in case["labels"].get(address, [])}


def _edges_with_counterparty(case: dict) -> list[tuple[dict, str]]:
    """Reconstructs, for every trace edge, which endpoint is the subject
    side (nearer) and which is the newly-discovered counterparty (farther)
    — trace_edges only stores from/to and hop, not which side the walk was
    expanding from, so this rebuilds it the same way trace.run_trace built
    it: hop 1 touches the subject directly, and each later hop's near set
    is everything discovered by the previous hop.
    """
    subject = case["subject"]
    edges = case["trace"]["edges"]
    near: set[str] = {subject}
    out: list[tuple[dict, str]] = []

    max_hop = max((e["hop"] for e in edges), default=0)
    for hop in range(1, max_hop + 1):
        discovered_this_hop: set[str] = set()
        for e in edges:
            if e["hop"] != hop:
                continue
            if e["from_address"] in near:
                counterparty = e["to_address"]
            elif e["to_address"] in near:
                counterparty = e["from_address"]
            else:
                counterparty = e["to_address"]  # self-loop or anomaly; harmless fallback
            out.append((e, counterparty))
            discovered_this_hop.add(counterparty)
        near |= discovered_this_hop
    return out


def _gaps(case: dict, max_hop: int | None = None) -> list[str]:
    """Human-readable gap descriptions for edges up to `max_hop` (or the
    whole trace if None). An empty list means complete for that scope.
    """
    edges = case["trace"]["edges"]
    if max_hop is not None:
        edges = [e for e in edges if e["hop"] <= max_hop]

    counts: dict[tuple[int, str], int] = {}
    for e in edges:
        if e["terminal_reason"] in GAP_TERMINAL_REASONS:
            key = (e["hop"], e["terminal_reason"])
            counts[key] = counts.get(key, 0) + 1

    gaps = [f"{n} hop-{hop} node(s) {reason}" for (hop, reason), n in sorted(counts.items())]

    # A fan_out_cap on the subject itself (hop 1, before any edges exist)
    # leaves zero edges and only a run-level note — surface it the same way.
    note = case["trace"].get("note")
    if note and "fan_out_cap" in note:
        gaps.append(note)
    return gaps


def sanctioned_exposure(case: dict) -> dict:
    """Value received from or sent to any address labelled `sanctioned`,
    direct and via trace paths, with hop distance recorded. Restricted to
    native ETH for the numeric total — comparing raw units across assets
    without a price would be inventing a fact not in the evidence (same
    convention used throughout ingest/enrich for value ranking).
    """
    subject = case["subject"]
    touches = []
    received_raw = 0
    sent_raw = 0

    for edge, counterparty in _edges_with_counterparty(case):
        if counterparty == subject:
            continue  # this is the subject's own status, not exposure to another party
        if "sanctioned" not in _categories_of(case, counterparty):
            continue
        # Direction is relative to the sanctioned node itself: did IT send
        # or receive the value on this edge.
        direction = "received" if edge["to_address"] == counterparty else "sent"
        touches.append(
            {
                "hop": edge["hop"],
                "address": counterparty,
                "tx_hash": edge["tx_hash"],
                "direction": direction,
            }
        )
        if edge["asset_address"] is None:
            amount = int(edge["amount_raw"])
            if direction == "received":
                sent_raw += amount  # subject sent value that a sanctioned node received
            else:
                received_raw += amount  # subject received value a sanctioned node sent

    evidence = sorted({t["tx_hash"] for t in touches} | {t["address"] for t in touches})
    gaps = _gaps(case)
    return {
        "name": "sanctioned_exposure",
        "value": {
            "received_from_sanctioned_raw_eth": str(received_raw),
            "sent_to_sanctioned_raw_eth": str(sent_raw),
            "touches": touches,
        },
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "evidence": evidence,
    }


def mixer_interaction(case: dict) -> dict:
    """Transfers to or from any address labelled `mixer`, with direction
    and count. Direction matters: receiving from a mixer and sending to
    one are different facts.
    """
    sent_to_mixer = []
    received_from_mixer = []

    for edge, counterparty in _edges_with_counterparty(case):
        if "mixer" not in _categories_of(case, counterparty):
            continue
        if edge["to_address"] == counterparty:
            sent_to_mixer.append(edge["tx_hash"])
        else:
            received_from_mixer.append(edge["tx_hash"])

    evidence = sorted(set(sent_to_mixer) | set(received_from_mixer))
    gaps = _gaps(case)
    return {
        "name": "mixer_interaction",
        "value": {
            "sent_to_mixer_count": len(sent_to_mixer),
            "received_from_mixer_count": len(received_from_mixer),
        },
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "evidence": evidence,
    }


def pass_through(case: dict) -> dict:
    """Value received and forwarded on again within PASS_THROUGH_WINDOW_BLOCKS,
    where the forwarded amount is within PASS_THROUGH_TOLERANCE_PCT of the
    received amount. Computed only from the subject's own direct (hop-1)
    transfers — this is a fact about the subject's own behaviour, not the
    wider graph, and native ETH only for the same reason as elsewhere.
    """
    subject = case["subject"]
    hop1 = [e for e in case["trace"]["edges"] if e["hop"] == 1 and e["asset_address"] is None]

    received = []
    sent = []
    for e in hop1:
        if e["block_number"] is None:
            continue  # no parent transaction (see ingest's orphan-transfer guard) — can't place it in time
        amount = int(e["amount_raw"])
        if e["to_address"] == subject:
            received.append((e["block_number"], amount, e["tx_hash"]))
        elif e["from_address"] == subject:
            sent.append((e["block_number"], amount, e["tx_hash"]))

    matches = []
    for r_block, r_amount, r_tx in received:
        for s_block, s_amount, s_tx in sent:
            if s_tx == r_tx:
                continue
            if not (r_block <= s_block <= r_block + PASS_THROUGH_WINDOW_BLOCKS):
                continue
            if r_amount == 0:
                continue
            if abs(s_amount - r_amount) / r_amount > PASS_THROUGH_TOLERANCE_PCT:
                continue
            matches.append({"received_tx": r_tx, "forwarded_tx": s_tx, "blocks_apart": s_block - r_block})

    evidence = sorted({m["received_tx"] for m in matches} | {m["forwarded_tx"] for m in matches})
    gaps = _gaps(case, max_hop=1)
    return {
        "name": "pass_through",
        "value": {"match_count": len(matches), "matches": matches},
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "evidence": evidence,
    }


def counterparty_concentration(case: dict) -> dict:
    """Share of total native-ETH value flowing to the single largest
    direct counterparty, and the count of distinct direct counterparties.
    """
    subject = case["subject"]
    hop1 = [e for e in case["trace"]["edges"] if e["hop"] == 1 and e["asset_address"] is None]

    totals: dict[str, int] = {}
    for e in hop1:
        counterparty = e["to_address"] if e["from_address"] == subject else e["from_address"]
        totals[counterparty] = totals.get(counterparty, 0) + int(e["amount_raw"])

    grand_total = sum(totals.values())
    if totals:
        top_address, top_value = max(totals.items(), key=lambda kv: kv[1])
        share = top_value / grand_total if grand_total else 0.0
    else:
        top_address, top_value, share = None, 0, 0.0

    gaps = _gaps(case, max_hop=1)
    return {
        "name": "counterparty_concentration",
        "value": {
            "top_counterparty": top_address,
            "top_counterparty_share": share,
            "distinct_counterparties": len(totals),
        },
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "evidence": [top_address] if top_address else [],
    }


def unlabelled_share(case: dict) -> dict:
    """Proportion of direct counterparties with no label at all. A
    confidence signal, not a risk signal: a subject whose counterparties
    are mostly unknown cannot be meaningfully assessed either way.
    """
    subject = case["subject"]
    hop1 = case["trace"]["edges"]
    hop1 = [e for e in hop1 if e["hop"] == 1]

    counterparties = {e["to_address"] if e["from_address"] == subject else e["from_address"] for e in hop1}
    counterparties.discard(subject)

    unlabelled = [a for a in counterparties if not case["labels"].get(a)]
    share = len(unlabelled) / len(counterparties) if counterparties else 0.0

    gaps = _gaps(case, max_hop=1)
    return {
        "name": "unlabelled_share",
        "value": {
            "unlabelled_share": share,
            "unlabelled_count": len(unlabelled),
            "total_counterparties": len(counterparties),
        },
        "complete": len(gaps) == 0,
        "gaps": gaps,
        "evidence": sorted(unlabelled),
    }


ALL_SIGNALS = [
    sanctioned_exposure,
    mixer_interaction,
    pass_through,
    counterparty_concentration,
    unlabelled_share,
]


def compute_all(case: dict) -> list[dict]:
    return [signal_fn(case) for signal_fn in ALL_SIGNALS]
