"""gate/rules.py tests. Every case here is a hand-written dict — no
database, no fixtures, no mocks, no import from ingest/ or enrich/. See
test_rules_module_imports_nothing_from_ingest_or_enrich below for a
mechanical check of that claim against gate/rules.py itself, and this
file's own import list above (there is no ingest/enrich import to see).
"""

from gate import rules, verdict

SUBJECT = "0xsubject"


def make_case(edges=None, labels=None, signals=None):
    return {
        "chain_id": "ethereum",
        "subject": SUBJECT,
        "labels": labels or {},
        "trace": {"trace_id": 1, "hops": 2, "direction": "both", "edges": edges or []},
        "signals": signals or [],
    }


def edge(hop, from_a, to_a, tx_hash, terminal_reason=None):
    return {
        "hop": hop, "from_address": from_a, "to_address": to_a, "tx_hash": tx_hash,
        "transfer_index": 0, "asset_address": None, "amount_raw": "1000000000000000000",
        "terminal_reason": terminal_reason,
    }


def label(category, name="test", source="etherscan.io/hand-sourced"):
    return [{"label": name, "category": category, "source": source, "retrieved": "2026-09-16"}]


def sanctioned_exposure_signal(touches, complete):
    return {"name": "sanctioned_exposure", "value": {"touches": touches}, "complete": complete, "gaps": {k: [] if v else [f"gap at {k}"] for k, v in complete.items()}, "evidence": []}


def mixer_signal(complete=True, gaps=None):
    return {"name": "mixer_interaction", "value": {}, "complete": complete, "gaps": gaps or [], "evidence": []}


def pass_through_signal(match_count, complete=True, gaps=None, evidence=None):
    return {"name": "pass_through", "value": {"match_count": match_count}, "complete": complete, "gaps": gaps or [], "evidence": evidence or []}


def unlabelled_signal(share, total, sufficient=True):
    return {
        "name": "unlabelled_share",
        "value": {"unlabelled_share": share, "unlabelled_count": int(share * total), "total_counterparties": total, "sufficient_sample": sufficient},
        "complete": sufficient,
        "gaps": [],
        "evidence": [],
    }


# --- subject_sanctioned ---

def test_subject_sanctioned_flags_when_subject_has_the_label():
    case = make_case(labels={SUBJECT: label("sanctioned", name="OFAC Blocked")})
    result = rules.subject_sanctioned(case)
    assert result["outcome"] == "FLAG"
    assert "OFAC Blocked" in result["reason"]
    assert result["evidence"] == [SUBJECT]


def test_subject_sanctioned_passes_when_not_labelled():
    case = make_case(labels={})
    result = rules.subject_sanctioned(case)
    assert result["outcome"] == "PASS"


def test_subject_sanctioned_notes_unverified_source():
    case = make_case(labels={SUBJECT: label("sanctioned", source="github.com/x/y (third-party Etherscan scrape, unverified)")})
    result = rules.subject_sanctioned(case)
    assert "unverified" in result["reason"]


# --- sanctioned_direct / sanctioned_indirect ---

def test_sanctioned_direct_flags_regardless_of_gaps():
    """The core of the third correction: a finding FLAGs even when the
    signal reports incomplete — an un-ingested node elsewhere doesn't
    unfind a transfer we actually saw.
    """
    case = make_case(
        edges=[edge(1, SUBJECT, "0xsanctioned", "0xtx1")],
        labels={"0xsanctioned": label("sanctioned")},
        signals=[sanctioned_exposure_signal(
            touches=[{"hop": 1, "address": "0xsanctioned", "tx_hash": "0xtx1", "direction": "received"}],
            complete={"hop_1": False},
        )],
    )
    result = rules.sanctioned_direct(case)
    assert result["outcome"] == "FLAG"
    assert "may be greater than measured" in result["reason"]
    assert "0xtx1" in result["evidence"]


def test_sanctioned_direct_passes_when_no_touch_and_complete():
    case = make_case(signals=[sanctioned_exposure_signal(touches=[], complete={"hop_1": True})])
    result = rules.sanctioned_direct(case)
    assert result["outcome"] == "PASS"


def test_sanctioned_direct_unknown_when_no_touch_and_incomplete():
    case = make_case(signals=[sanctioned_exposure_signal(touches=[], complete={"hop_1": False})])
    result = rules.sanctioned_direct(case)
    assert result["outcome"] == "UNKNOWN"


def test_sanctioned_indirect_flags_hop2_regardless_of_gaps():
    case = make_case(
        labels={"0xsanctioned": label("sanctioned")},
        edges=[edge(1, SUBJECT, "0xmid", "0xtx1"), edge(2, "0xmid", "0xsanctioned", "0xtx2")],
        signals=[sanctioned_exposure_signal(
            touches=[{"hop": 2, "address": "0xsanctioned", "tx_hash": "0xtx2", "direction": "received"}],
            complete={"hop_1": True, "hop_2": False},
        )],
    )
    result = rules.sanctioned_indirect(case)
    assert result["outcome"] == "FLAG"
    assert "within 2 hops" in result["reason"]


def test_sanctioned_indirect_unknown_when_hops_did_not_extend_past_one():
    case = make_case(edges=[edge(1, SUBJECT, "0xa", "0xtx1")], signals=[sanctioned_exposure_signal(touches=[], complete={"hop_1": True})])
    result = rules.sanctioned_indirect(case)
    assert result["outcome"] == "UNKNOWN"


def test_sanctioned_indirect_passes_when_deep_and_complete():
    case = make_case(
        edges=[edge(1, SUBJECT, "0xa", "0xtx1"), edge(2, "0xa", "0xb", "0xtx2")],
        signals=[sanctioned_exposure_signal(touches=[], complete={"hop_1": True, "hop_2": True})],
    )
    result = rules.sanctioned_indirect(case)
    assert result["outcome"] == "PASS"


# --- mixer_outbound ---

def test_mixer_outbound_flags_only_outbound_direction():
    case = make_case(
        edges=[edge(1, SUBJECT, "0xmixer", "0xtx1")],
        labels={"0xmixer": label("mixer")},
        signals=[mixer_signal(complete=False)],
    )
    result = rules.mixer_outbound(case)
    assert result["outcome"] == "FLAG"
    assert "may be greater than measured" in result["reason"]


def test_mixer_outbound_ignores_inbound_from_mixer():
    case = make_case(
        edges=[edge(1, "0xmixer", SUBJECT, "0xtx1")],  # received FROM mixer, not sent
        labels={"0xmixer": label("mixer")},
        signals=[mixer_signal(complete=True)],
    )
    result = rules.mixer_outbound(case)
    assert result["outcome"] == "PASS"


def test_mixer_outbound_unknown_when_incomplete_and_nothing_found():
    case = make_case(signals=[mixer_signal(complete=False, gaps=["1 hop-1 node(s) not_ingested"])])
    result = rules.mixer_outbound(case)
    assert result["outcome"] == "UNKNOWN"


# --- rapid_pass_through ---

def test_rapid_pass_through_flags_at_threshold():
    case = make_case(signals=[pass_through_signal(match_count=1, complete=True, evidence=["0xtx1", "0xtx2"])])
    result = rules.rapid_pass_through(case)
    assert result["outcome"] == "FLAG"
    assert result["evidence"] == ["0xtx1", "0xtx2"]


def test_rapid_pass_through_flags_even_when_incomplete():
    case = make_case(signals=[pass_through_signal(match_count=1, complete=False)])
    result = rules.rapid_pass_through(case)
    assert result["outcome"] == "FLAG"


def test_rapid_pass_through_passes_when_zero_and_complete():
    case = make_case(signals=[pass_through_signal(match_count=0, complete=True)])
    result = rules.rapid_pass_through(case)
    assert result["outcome"] == "PASS"


# --- compute_confidence ---
# unassessable is no longer a rule: it's a statement about the case
# (confidence), kept off the rule list so it never competes with a
# finding. See gate/verdict.py for how it still forces UNASSESSABLE
# on an otherwise-CLEAR verdict.

def test_confidence_low_on_tiny_sample():
    case = make_case(
        edges=[edge(1, SUBJECT, "0xa", "0xtx1")],
        signals=[unlabelled_signal(share=0.0, total=1, sufficient=False)],
    )
    result = rules.compute_confidence(case)
    assert result["level"] == "low"
    assert "minimum sample" in result["reason"]


def test_confidence_low_on_majority_unlabelled():
    case = make_case(
        edges=[edge(1, SUBJECT, f"0x{i}", f"0xtx{i}") for i in range(20)],
        signals=[unlabelled_signal(share=0.8, total=20, sufficient=True)],
    )
    result = rules.compute_confidence(case)
    assert result["level"] == "low"


def test_confidence_low_on_majority_not_ingested():
    edges = [edge(1, SUBJECT, f"0x{i}", f"0xtx{i}", terminal_reason="not_ingested") for i in range(15)]
    edges += [edge(1, SUBJECT, f"0xok{i}", f"0xtxok{i}") for i in range(5)]
    case = make_case(edges=edges, signals=[unlabelled_signal(share=0.0, total=20, sufficient=True)])
    result = rules.compute_confidence(case)
    assert result["level"] == "low"
    assert "not_ingested" in result["reason"]


def test_confidence_high_with_good_coverage():
    edges = [edge(1, SUBJECT, f"0x{i}", f"0xtx{i}") for i in range(20)]
    case = make_case(edges=edges, signals=[unlabelled_signal(share=0.1, total=20, sufficient=True)])
    result = rules.compute_confidence(case)
    assert result["level"] == "high"


# --- verdict ---

def test_verdict_clear_when_every_rule_passes_and_confidence_is_high():
    rule_results = [{"rule": r, "outcome": "PASS", "reason": "", "evidence": [], "signal": None} for r in
                    ["subject_sanctioned", "sanctioned_direct", "sanctioned_indirect", "mixer_outbound", "rapid_pass_through"]]
    assert verdict.compute_verdict(rule_results, {"level": "high"}) == "CLEAR"


def test_verdict_unassessable_on_single_unknown():
    rule_results = [{"rule": "subject_sanctioned", "outcome": "PASS", "reason": "", "evidence": [], "signal": None},
                    {"rule": "sanctioned_direct", "outcome": "UNKNOWN", "reason": "", "evidence": [], "signal": None}]
    assert verdict.compute_verdict(rule_results, {"level": "high"}) == "UNASSESSABLE"


def test_verdict_flagged_on_a_finding_regardless_of_unknowns_or_gaps():
    """The core of this correction: any FLAG means FLAGGED, full stop —
    a verified finding is not weakened by an UNKNOWN elsewhere in the case.
    """
    rule_results = [{"rule": "subject_sanctioned", "outcome": "UNKNOWN", "reason": "", "evidence": [], "signal": None},
                    {"rule": "sanctioned_direct", "outcome": "FLAG", "reason": "", "evidence": [], "signal": None}]
    assert verdict.compute_verdict(rule_results, {"level": "low"}) == "FLAGGED"


def test_verdict_clear_with_low_confidence_is_impossible():
    """The invariant this correction adds: CLEAR + low confidence must
    never happen, regardless of what the individual rules concluded —
    that combination is exactly what UNKNOWN exists to prevent.
    """
    rule_results = [{"rule": r, "outcome": "PASS", "reason": "", "evidence": [], "signal": None} for r in
                    ["subject_sanctioned", "sanctioned_direct", "sanctioned_indirect", "mixer_outbound", "rapid_pass_through"]]
    assert verdict.compute_verdict(rule_results, {"level": "low"}) == "UNASSESSABLE"


def test_rules_module_imports_nothing_from_ingest_or_enrich():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(rules))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    assert imported_modules.isdisjoint({"ingest", "enrich", "sqlite3"}), imported_modules
