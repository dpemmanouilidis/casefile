"""narrate/verify.py tests. Every case here is a hand-written narrative
string plus a hand-written Case/Verdict dict — no database, no model, no
imports from ingest/ or enrich/. See
test_verify_module_imports_nothing_from_ingest_or_enrich below for a
mechanical check of that claim against narrate/verify.py itself.
"""

from narrate import verify

SUBJECT = "0x" + "1" * 40
REAL_TX = "0x" + "aa" * 32
REAL_TX_2 = "0x" + "bb" * 32
FAKE_TX = "0x" + "cc" * 32
COUNTERPARTY = "0x" + "2" * 40
FAKE_ADDRESS = "0x" + "3" * 40


def make_case(edges=None, labels=None, signals=None):
    return {
        "chain_id": "ethereum",
        "subject": SUBJECT,
        "labels": labels or {},
        "trace": {"trace_id": 1, "hops": 2, "direction": "both", "edges": edges or []},
        "signals": signals or [],
    }


def edge(hop, from_a, to_a, tx_hash):
    return {
        "hop": hop, "from_address": from_a, "to_address": to_a, "tx_hash": tx_hash,
        "transfer_index": 0, "asset_address": None, "amount_raw": "1000000000000000000",
        "terminal_reason": None,
    }


def make_verdict(verdict="FLAGGED", confidence_level="high", rules=None):
    return {
        "subject": SUBJECT,
        "verdict": verdict,
        "confidence": {"level": confidence_level},
        "rules": rules or [],
        "case_id": 1,
    }


def flag_rule(rule_id, evidence=None):
    return {"rule": rule_id, "outcome": "FLAG", "reason": "test", "evidence": evidence or [], "signal": None}


BASE_CASE = make_case(edges=[edge(1, SUBJECT, COUNTERPARTY, REAL_TX)])


# --- check_citation_existence ---

def test_citation_existence_passes_when_hash_is_in_case():
    text = f"The subject sent funds to {COUNTERPARTY} in {REAL_TX}."
    result = verify.check_citation_existence(text, BASE_CASE)
    assert result["passed"]
    assert result["fabricated_hashes"] == []


def test_citation_existence_rejects_fabricated_hash():
    text = f"The subject sent funds in {FAKE_TX}, a transaction not in the case."
    result = verify.check_citation_existence(text, BASE_CASE)
    assert not result["passed"]
    assert result["fabricated_hashes"] == [FAKE_TX.lower()]


def test_citation_existence_is_case_insensitive():
    text = f"Transaction {REAL_TX.upper()} moved funds."
    result = verify.check_citation_existence(text, BASE_CASE)
    assert result["passed"]


# --- check_address_existence ---

def test_address_existence_passes_when_address_is_in_case():
    text = f"Funds moved from {SUBJECT} to {COUNTERPARTY}."
    result = verify.check_address_existence(text, BASE_CASE)
    assert result["passed"]


def test_address_existence_rejects_fabricated_address():
    text = f"Funds also touched {FAKE_ADDRESS}, an address absent from the case."
    result = verify.check_address_existence(text, BASE_CASE)
    assert not result["passed"]
    assert result["fabricated_addresses"] == [FAKE_ADDRESS.lower()]


def test_address_existence_finds_addresses_anywhere_in_case_not_just_edges():
    case = make_case(labels={COUNTERPARTY: [{"label": "mixer", "category": "mixer", "source": "x", "retrieved": "2026-01-01"}]})
    text = f"The counterparty {COUNTERPARTY} is a known mixer."
    result = verify.check_address_existence(text, case)
    assert result["passed"]


# --- check_verdict_fidelity / check_confidence_fidelity (structural header) ---

def header(verdict_word, confidence_word, flags="NONE"):
    return f"VERDICT: {verdict_word} (confidence: {confidence_word})\nFLAGS: {flags}"


def test_verdict_fidelity_passes_when_header_matches():
    text = f"{header('FLAGGED', 'high')}\nBased on direct sanctioned exposure."
    result = verify.check_verdict_fidelity(text, make_verdict(verdict="FLAGGED"))
    assert result["passed"]


def test_verdict_fidelity_rejects_missing_header():
    text = "The subject moved funds through several counterparties, and is FLAGGED."
    result = verify.check_verdict_fidelity(text, make_verdict(verdict="FLAGGED"))
    assert not result["passed"]
    assert not result["header_found"]


def test_verdict_fidelity_rejects_contradicting_header():
    text = f"{header('CLEAR', 'high')}\nNo exposure was found."
    result = verify.check_verdict_fidelity(text, make_verdict(verdict="FLAGGED"))
    assert not result["passed"]
    assert result["stated_verdict"] == "CLEAR"


def test_verdict_fidelity_ignores_verdict_words_in_ordinary_prose_below_header():
    # "unassessable" used as an ordinary English word describing an UNKNOWN
    # rule's own finding must not be misread as a second verdict statement —
    # only the header line is checked.
    text = f"{header('FLAGGED', 'low')}\nIndirect exposure is unassessable at this hop depth."
    result = verify.check_verdict_fidelity(text, make_verdict(verdict="FLAGGED"))
    assert result["passed"]


# --- check_confidence_fidelity ---

def test_confidence_fidelity_passes_when_header_matches_high():
    text = f"{header('FLAGGED', 'high')}\nDetails follow."
    result = verify.check_confidence_fidelity(text, make_verdict(confidence_level="high"))
    assert result["passed"]


def test_confidence_fidelity_passes_when_header_matches_low():
    text = f"{header('UNASSESSABLE', 'low')}\nDetails follow."
    result = verify.check_confidence_fidelity(text, make_verdict(verdict="UNASSESSABLE", confidence_level="low"))
    assert result["passed"]


def test_confidence_fidelity_rejects_wrong_confidence_token():
    text = f"{header('UNASSESSABLE', 'high')}\nDetails follow."
    result = verify.check_confidence_fidelity(text, make_verdict(verdict="UNASSESSABLE", confidence_level="low"))
    assert not result["passed"]


def test_confidence_fidelity_rejects_missing_header():
    text = "The subject is UNASSESSABLE based on incomplete trace coverage."
    result = verify.check_confidence_fidelity(text, make_verdict(verdict="UNASSESSABLE", confidence_level="low"))
    assert not result["passed"]
    assert not result["header_found"]


# --- check_finding_coverage ---

def test_finding_coverage_passes_when_flags_line_matches_flag_rules():
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct, rapid_pass_through')}\nDetails follow."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct"), flag_rule("rapid_pass_through")])
    result = verify.check_finding_coverage(text, verdict)
    assert result["passed"]


def test_finding_coverage_passes_with_none_when_no_flag_rules():
    text = f"{header('CLEAR', 'high', flags='NONE')}\nDetails follow."
    verdict = make_verdict(verdict="CLEAR", rules=[])
    result = verify.check_finding_coverage(text, verdict)
    assert result["passed"]


def test_finding_coverage_rejects_missing_flag_rule():
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct')}\nDetails follow."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct"), flag_rule("rapid_pass_through")])
    result = verify.check_finding_coverage(text, verdict)
    assert not result["passed"]
    assert result["missing"] == ["rapid_pass_through"]


def test_finding_coverage_rejects_invented_rule_id():
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct, made_up_rule')}\nDetails follow."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct")])
    result = verify.check_finding_coverage(text, verdict)
    assert not result["passed"]
    assert result["extra"] == ["made_up_rule"]


def test_finding_coverage_rejects_missing_flags_line():
    text = "VERDICT: FLAGGED (confidence: high)\nSome narrative with no FLAGS line."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct")])
    result = verify.check_finding_coverage(text, verdict)
    assert not result["passed"]
    assert not result["flags_line_found"]


# --- check_finding_substantiation ---

def test_finding_substantiation_passes_when_each_rule_cites_at_least_one_hash():
    text = (
        f"{header('FLAGGED', 'high', flags='sanctioned_direct, rapid_pass_through')}\n"
        f"Direct exposure via {REAL_TX}. Pass-through via {REAL_TX_2}."
    )
    verdict = make_verdict(rules=[
        flag_rule("sanctioned_direct", evidence=[REAL_TX, COUNTERPARTY]),
        flag_rule("rapid_pass_through", evidence=[REAL_TX_2]),
    ])
    result = verify.check_finding_substantiation(text, verdict)
    assert result["passed"]


def test_finding_substantiation_passes_with_only_one_of_several_evidence_items_cited():
    # Scoped per finding, not per hash: one citation for a rule with many
    # evidence items is enough — eval/sweep.py's evidence_mention_rate is
    # what measures the fuller fraction, this check only gates substantiation.
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct')}\nEvidenced by {REAL_TX}."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct", evidence=[REAL_TX, REAL_TX_2, COUNTERPARTY])])
    result = verify.check_finding_substantiation(text, verdict)
    assert result["passed"]


def test_finding_substantiation_rejects_announced_but_uncited_finding():
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct')}\nThe subject was flagged for sanctions exposure."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct", evidence=[REAL_TX])])
    result = verify.check_finding_substantiation(text, verdict)
    assert not result["passed"]
    assert result["unsubstantiated"] == ["sanctioned_direct"]


def test_finding_substantiation_ignores_rule_with_no_evidence_to_cite():
    text = f"{header('FLAGGED', 'high', flags='subject_sanctioned')}\nNo hashes needed here."
    verdict = make_verdict(rules=[flag_rule("subject_sanctioned", evidence=[])])
    result = verify.check_finding_substantiation(text, verdict)
    assert result["passed"]


def test_finding_substantiation_skips_invented_rule_id_not_in_verdict():
    # An id with no matching rule at all is check 6's failure, not this one's.
    text = f"{header('FLAGGED', 'high', flags='made_up_rule')}\nSome text."
    verdict = make_verdict(rules=[])
    result = verify.check_finding_substantiation(text, verdict)
    assert result["passed"]
    assert result["unsubstantiated"] == []


def test_finding_substantiation_rejects_missing_flags_line():
    text = "VERDICT: FLAGGED (confidence: high)\nNo structured findings block here."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct", evidence=[REAL_TX])])
    result = verify.check_finding_substantiation(text, verdict)
    assert not result["passed"]
    assert not result["flags_line_found"]


def test_finding_substantiation_does_not_count_a_citation_in_the_flags_line_itself():
    # Evidence hashes never legitimately appear in the structural lines —
    # this guards against the search corpus accidentally including them.
    text = f"{header('FLAGGED', 'high', flags='sanctioned_direct')}\nUnrelated prose with no citation."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct", evidence=["sanctioned_direct"])])
    result = verify.check_finding_substantiation(text, verdict)
    assert not result["passed"]


# --- check_uncited_assertion ---

def test_uncited_assertion_flags_sentence_with_verb_and_no_citation():
    text = "The subject transferred a large sum to an unnamed party."
    result = verify.check_uncited_assertion(text, make_verdict())
    assert len(result["flagged_sentences"]) == 1


def test_uncited_assertion_does_not_flag_cited_sentence():
    text = f"The subject sent funds to {COUNTERPARTY} in {REAL_TX}."
    result = verify.check_uncited_assertion(text, make_verdict())
    assert result["flagged_sentences"] == []


def test_uncited_assertion_does_not_flag_sentence_without_activity_verb():
    text = "The overall risk profile of this subject is notable."
    result = verify.check_uncited_assertion(text, make_verdict())
    assert result["flagged_sentences"] == []


def test_uncited_assertion_exempts_sentence_citing_a_rule_id():
    # Class 1 from the hand adjudication: a gap/reason restatement that
    # names the rule id instead of a hash — a valid citation now, since
    # the rule id is enumerable straight from the Verdict.
    text = "Indirect exposure was received but could not be assessed further (sanctioned_indirect)."
    verdict = make_verdict(rules=[flag_rule("sanctioned_indirect")])
    result = verify.check_uncited_assertion(text, verdict)
    assert result["flagged_sentences"] == []


def test_uncited_assertion_exempts_confidence_sentence():
    # Class 2: describing the confidence field is not a transaction fact.
    text = "The subject received low confidence due to a thin sample of counterparties."
    result = verify.check_uncited_assertion(text, make_verdict())
    assert result["flagged_sentences"] == []


def test_uncited_assertion_still_flags_unrelated_uncited_sentence_with_rules_present():
    # A rule id being known to the Verdict doesn't blanket-exempt every
    # sentence — only ones that actually name a rule id.
    text = "The subject transferred funds to an unnamed counterparty."
    verdict = make_verdict(rules=[flag_rule("sanctioned_direct")])
    result = verify.check_uncited_assertion(text, verdict)
    assert len(result["flagged_sentences"]) == 1


# --- check_intensifier_language ---

def test_intensifier_language_flags_immediately_near_rapid_pass_through_evidence():
    text = f"The subject received and immediately forwarded value, evidenced by {REAL_TX}."
    verdict = make_verdict(rules=[flag_rule("rapid_pass_through", evidence=[REAL_TX])])
    result = verify.check_intensifier_language(text, verdict)
    assert len(result["flagged_sentences"]) == 1


def test_intensifier_language_does_not_flag_without_rapid_pass_through_evidence_in_sentence():
    text = f"The subject received and immediately forwarded value. Unrelated citation: {REAL_TX}."
    verdict = make_verdict(rules=[flag_rule("rapid_pass_through", evidence=[REAL_TX_2])])
    result = verify.check_intensifier_language(text, verdict)
    assert result["flagged_sentences"] == []


def test_intensifier_language_does_not_flag_when_no_intensifier_present():
    text = f"The subject received and then forwarded value, evidenced by {REAL_TX}."
    verdict = make_verdict(rules=[flag_rule("rapid_pass_through", evidence=[REAL_TX])])
    result = verify.check_intensifier_language(text, verdict)
    assert result["flagged_sentences"] == []


def test_intensifier_language_never_affects_passed():
    # Advisory only — never rejects, even when it fires.
    text = (
        f"{header('FLAGGED', 'high', flags='rapid_pass_through')}\n"
        f"Value was immediately forwarded, evidenced by {REAL_TX}."
    )
    verdict = make_verdict(rules=[flag_rule("rapid_pass_through", evidence=[REAL_TX])])
    result = verify.verify_narrative(text, BASE_CASE, verdict)
    assert result["passed"]
    assert len(result["flags"]["intensifier_language"]["flagged_sentences"]) == 1


def test_intensifier_language_empty_when_no_rapid_pass_through_rule():
    text = "The subject immediately did something unrelated."
    result = verify.check_intensifier_language(text, make_verdict(rules=[]))
    assert result["flagged_sentences"] == []


# --- verify_narrative orchestration ---

def test_verify_narrative_passes_clean_narrative():
    text = f"{header('FLAGGED', 'high')}\nThe subject {SUBJECT} sent funds to {COUNTERPARTY} in {REAL_TX}."
    result = verify.verify_narrative(text, BASE_CASE, make_verdict(verdict="FLAGGED", confidence_level="high"))
    assert result["passed"]


def test_verify_narrative_fails_on_fabricated_hash_even_if_header_correct():
    text = f"{header('FLAGGED', 'high')}\nEvidenced by transaction {FAKE_TX}."
    result = verify.verify_narrative(text, BASE_CASE, make_verdict(verdict="FLAGGED", confidence_level="high"))
    assert not result["passed"]
    assert not result["checks"]["citation_existence"]["passed"]


def test_verify_narrative_check_5_flags_do_not_block_pass():
    text = (
        f"{header('FLAGGED', 'high')}\n"
        f"The subject {SUBJECT} sent funds to {COUNTERPARTY} in {REAL_TX}. "
        "The subject also interacted with several other parties."
    )
    result = verify.verify_narrative(text, BASE_CASE, make_verdict(verdict="FLAGGED", confidence_level="high"))
    assert result["passed"]
    assert len(result["flags"]["uncited_assertion"]["flagged_sentences"]) == 1


# --- import boundary ---

def test_verify_module_imports_nothing_from_ingest_or_enrich():
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(verify))
    imported_modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.add(node.module.split(".")[0])

    assert imported_modules.isdisjoint({"ingest", "enrich", "sqlite3"}), imported_modules
