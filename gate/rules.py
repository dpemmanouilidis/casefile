"""Pure functions from Case to rule outcomes. No I/O, no model, no network,
no imports from ingest/ or enrich/ — every function here is callable with
a hand-written dict, because the Case dict (see enrich/case.py) already
carries everything a rule needs, signals included.

## How a finding relates to completeness

Completeness gates the negative answer only:

- Evidence found -> FLAG, regardless of gaps. Finding a sanctioned
  transfer is a positive fact; an un-ingested node elsewhere doesn't
  unfind it. Where gaps exist in the relevant scope, the reason notes
  that actual exposure may be greater than measured.
- Nothing found, scope complete -> PASS.
- Nothing found, scope incomplete -> UNKNOWN.

The dangerous direction is claiming clean without having looked; claiming
flagged on evidence genuinely found is never the unsafe error. This is
why FLAG never depends on the completeness flag a signal reports — only
the PASS/UNKNOWN split for a non-finding does.

## Label tiers

No rule may consult a label without checking whether it rests on an
unverified third-party source (the bulk Etherscan scrape, see
enrich/labels/etherscan_labels_bulk.csv) versus a hand-sourced or official
one. A FLAG resting even partly on an unverified label says so in its
reason — see `_unverified_note`.
"""

from __future__ import annotations

RAPID_PASS_THROUGH_MIN_MATCHES = 1  # even a single received-then-forwarded match
# within the window/tolerance (see enrich/signals.py) is a real pass-through
# event — layering typically shows as isolated instances per subject rather
# than a repeated pattern, so requiring more than one would miss the common case.

UNASSESSABLE_UNLABELLED_THRESHOLD = 0.5  # more than half of direct counterparties
# unlabelled means we cannot tell whether this subject mostly associates with
# clean or risky parties — 0.5 is the natural "we know less than we don't" cutoff.
UNASSESSABLE_GAP_THRESHOLD = 0.5  # more than half of hop-1 nodes not_ingested or
# fan_out_cap: same rationale, applied to trace coverage instead of label coverage.


def _is_unverified(source: str | None) -> bool:
    return "unverified" in (source or "").lower()


def _labels_of(case: dict, address: str, category: str) -> list[dict]:
    return [row for row in case["labels"].get(address, []) if row["category"] == category]


def _unverified_note(label_rows: list[dict]) -> str | None:
    if any(_is_unverified(r["source"]) for r in label_rows):
        return "rests in part on an unverified third-party label"
    return None


def _signal(case: dict, name: str) -> dict:
    for sig in case["signals"]:
        if sig["name"] == name:
            return sig
    raise KeyError(f"case has no signal named {name!r}")


def _reason(rule: str, outcome: str, reason: str, evidence: list[str], signal: str | None) -> dict:
    return {"rule": rule, "outcome": outcome, "reason": reason, "evidence": evidence, "signal": signal}


def subject_sanctioned(case: dict) -> dict:
    """The subject itself carries a `sanctioned` label. Separate finding
    from exposure — never conflated with sanctioned_direct/indirect, which
    are about the subject's counterparties, not the subject's own status.
    """
    subject = case["subject"]
    rows = _labels_of(case, subject, "sanctioned")
    if rows:
        names = ", ".join(sorted({r["label"] for r in rows}))
        reason = f"the subject itself is labelled 'sanctioned' ({names})"
        note = _unverified_note(rows)
        if note:
            reason += f"; {note}"
        return _reason("subject_sanctioned", "FLAG", reason, evidence=[subject], signal=None)
    return _reason(
        "subject_sanctioned", "PASS", "the subject carries no 'sanctioned' label", evidence=[], signal=None
    )


def sanctioned_direct(case: dict) -> dict:
    """Direct (hop-1) transfer to or from an address labelled `sanctioned`.
    Excludes the subject's own label (subject_sanctioned's job).
    """
    sig = _signal(case, "sanctioned_exposure")
    subject = case["subject"]
    touches = [t for t in sig["value"]["touches"] if t["hop"] == 1 and t["address"] != subject]

    if touches:
        evidence = sorted({t["tx_hash"] for t in touches} | {t["address"] for t in touches})
        label_rows = [r for t in touches for r in _labels_of(case, t["address"], "sanctioned")]
        reason = "received or sent value directly (hop 1) to/from an address labelled 'sanctioned'"
        if not sig["complete"].get("hop_1", False):
            reason += "; additional un-ingested hop-1 counterparties mean actual exposure may be greater than measured"
        note = _unverified_note(label_rows)
        if note:
            reason += f"; {note}"
        return _reason("sanctioned_direct", "FLAG", reason, evidence, signal="sanctioned_exposure")

    if sig["complete"].get("hop_1", False):
        return _reason(
            "sanctioned_direct", "PASS",
            "no direct (hop 1) transfer to/from a sanctioned address, and hop-1 data is complete",
            evidence=[], signal="sanctioned_exposure",
        )
    gaps = "; ".join(sig["gaps"].get("hop_1", [])) or "hop-1 data incomplete"
    return _reason(
        "sanctioned_direct", "UNKNOWN",
        f"no direct sanctioned exposure found, but hop-1 data is incomplete: {gaps}",
        evidence=[], signal="sanctioned_exposure",
    )


def sanctioned_indirect(case: dict) -> dict:
    """A sanctioned address within N hops, N > 1 (hop 1 is
    sanctioned_direct's job). N is the shallowest hop at which one was
    found, recorded in the reason.
    """
    sig = _signal(case, "sanctioned_exposure")
    edges = case["trace"]["edges"]
    max_hop = max((e["hop"] for e in edges), default=0)
    indirect = [t for t in sig["value"]["touches"] if t["hop"] > 1]

    if indirect:
        n = min(t["hop"] for t in indirect)
        at_n = [t for t in indirect if t["hop"] == n]
        evidence = sorted({t["tx_hash"] for t in at_n} | {t["address"] for t in at_n})
        label_rows = [r for t in at_n for r in _labels_of(case, t["address"], "sanctioned")]
        reason = f"a sanctioned address found within {n} hops"
        if not sig["complete"].get(f"hop_{n}", False):
            reason += "; additional un-ingested nodes along the way mean actual exposure may be greater than measured"
        note = _unverified_note(label_rows)
        if note:
            reason += f"; {note}"
        return _reason("sanctioned_indirect", "FLAG", reason, evidence, signal="sanctioned_exposure")

    if max_hop < 2:
        return _reason(
            "sanctioned_indirect", "UNKNOWN",
            "trace did not extend beyond hop 1; indirect exposure is not assessable at this hop depth",
            evidence=[], signal="sanctioned_exposure",
        )
    if sig["complete"].get(f"hop_{max_hop}", False):
        return _reason(
            "sanctioned_indirect", "PASS",
            f"no sanctioned address found within {max_hop} hops, and hop 1..{max_hop} data is complete",
            evidence=[], signal="sanctioned_exposure",
        )
    gaps = "; ".join(sig["gaps"].get(f"hop_{max_hop}", [])) or "trace data incomplete"
    return _reason(
        "sanctioned_indirect", "UNKNOWN",
        f"no indirect sanctioned exposure found within {max_hop} hops, but data is incomplete: {gaps}",
        evidence=[], signal="sanctioned_exposure",
    )


def mixer_outbound(case: dict) -> dict:
    """Value sent (not received — direction matters) to an address
    labelled `mixer`, direct transfers only.
    """
    subject = case["subject"]
    sent = []
    for e in case["trace"]["edges"]:
        if e["hop"] != 1:
            continue
        counterparty = e["to_address"] if e["from_address"] == subject else e["from_address"]
        if e["to_address"] != counterparty:
            continue  # this edge is incoming to the subject, not outbound
        if "mixer" in {r["category"] for r in case["labels"].get(counterparty, [])}:
            sent.append((e["tx_hash"], counterparty))

    sig = _signal(case, "mixer_interaction")
    if sent:
        evidence = sorted({tx for tx, _ in sent} | {addr for _, addr in sent})
        label_rows = [r for _, addr in sent for r in _labels_of(case, addr, "mixer")]
        reason = "sent value directly to an address labelled 'mixer'"
        if not sig["complete"]:
            reason += "; additional un-ingested hop-1 counterparties mean actual exposure may be greater than measured"
        note = _unverified_note(label_rows)
        if note:
            reason += f"; {note}"
        return _reason("mixer_outbound", "FLAG", reason, evidence, signal="mixer_interaction")

    if sig["complete"]:
        return _reason(
            "mixer_outbound", "PASS",
            "no value sent to a mixer-labelled address, and hop-1 data is complete",
            evidence=[], signal="mixer_interaction",
        )
    gaps = "; ".join(sig["gaps"]) or "hop-1 data incomplete"
    return _reason(
        "mixer_outbound", "UNKNOWN",
        f"no mixer-outbound value found, but hop-1 data is incomplete: {gaps}",
        evidence=[], signal="mixer_interaction",
    )


def rapid_pass_through(case: dict) -> dict:
    """pass_through signal at or above RAPID_PASS_THROUGH_MIN_MATCHES."""
    sig = _signal(case, "pass_through")
    count = sig["value"]["match_count"]

    if count >= RAPID_PASS_THROUGH_MIN_MATCHES:
        reason = f"{count} received-then-forwarded match(es) at or above the threshold of {RAPID_PASS_THROUGH_MIN_MATCHES}"
        if not sig["complete"]:
            reason += "; additional un-ingested hop-1 counterparties mean the true pattern may be larger than measured"
        return _reason("rapid_pass_through", "FLAG", reason, evidence=sig["evidence"], signal="pass_through")

    if sig["complete"]:
        return _reason(
            "rapid_pass_through", "PASS",
            "no pass-through pattern found, and hop-1 data is complete",
            evidence=[], signal="pass_through",
        )
    gaps = "; ".join(sig["gaps"]) or "hop-1 data incomplete"
    return _reason(
        "rapid_pass_through", "UNKNOWN",
        f"no pass-through pattern found, but hop-1 data is incomplete: {gaps}",
        evidence=[], signal="pass_through",
    )


def unassessable(case: dict) -> dict:
    """Fires as FLAG on the case itself, not the subject: the evidence is
    too thin to judge, which is a finding in itself. Never UNKNOWN — that
    would be circular (unknown whether we know enough to know).
    """
    subject = case["subject"]
    unlab = _signal(case, "unlabelled_share")
    sufficient = unlab["value"]["sufficient_sample"]
    share = unlab["value"]["unlabelled_share"]
    total = unlab["value"]["total_counterparties"]

    subject_side_addrs = {subject}
    counterparty_gap: dict[str, str | None] = {}
    for e in case["trace"]["edges"]:
        if e["hop"] != 1:
            continue
        cp = e["to_address"] if e["from_address"] in subject_side_addrs else e["from_address"]
        if e["terminal_reason"] in {"not_ingested", "fan_out_cap"}:
            counterparty_gap[cp] = e["terminal_reason"]
        else:
            counterparty_gap.setdefault(cp, None)
    gap_fraction = (
        sum(1 for v in counterparty_gap.values() if v is not None) / len(counterparty_gap)
        if counterparty_gap else 0.0
    )

    findings = []
    if not sufficient:
        findings.append(f"only {total} direct counterpart(ies), below the minimum sample")
    elif share > UNASSESSABLE_UNLABELLED_THRESHOLD:
        findings.append(f"{share:.0%} of direct counterparties unlabelled (> {UNASSESSABLE_UNLABELLED_THRESHOLD:.0%})")
    if gap_fraction > UNASSESSABLE_GAP_THRESHOLD:
        findings.append(f"{gap_fraction:.0%} of hop-1 nodes not_ingested/fan_out_cap (> {UNASSESSABLE_GAP_THRESHOLD:.0%})")

    if findings:
        return _reason(
            "unassessable", "FLAG",
            "evidence too thin to judge this case: " + "; ".join(findings),
            evidence=[subject], signal="unlabelled_share",
        )
    return _reason(
        "unassessable", "PASS",
        "sufficient direct-counterparty coverage and labelling to assess this case",
        evidence=[], signal="unlabelled_share",
    )


ALL_RULES = [
    subject_sanctioned,
    sanctioned_direct,
    sanctioned_indirect,
    mixer_outbound,
    rapid_pass_through,
    unassessable,
]


def evaluate_all(case: dict) -> list[dict]:
    return [rule_fn(case) for rule_fn in ALL_RULES]
