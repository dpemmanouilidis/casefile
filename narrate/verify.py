"""Deterministic post-generation checks on a narrative. Pure functions, same
boundary rule as gate/: no I/O, no model, no network, no imports from
ingest/ or enrich/ — every function here is callable with a hand-written
narrative string and a hand-written Case/Verdict dict.

Checks 1-4, 6 and 7 (citation existence, address existence, verdict
fidelity, confidence fidelity, finding coverage, finding substantiation)
are exact and reject: any failure means the narrative is rejected
outright, `verify_narrative` returns passed=False, and the caller
(narrate/model.py) regenerates or falls back to the template. Checks 6
and 7 can be exact, unlike check 5, because the set of FLAG rules and
each one's evidence are enumerable straight from the Verdict — there's no
prose classification involved, just set/membership comparisons against a
mandated structured line (see narrate/prompt.py). Check 6 asks "did the
narrative announce every FLAG rule by id"; check 7 asks "for each rule it
announced, did it actually cite evidence for that specific rule" — an
announced-but-uncited finding is a different failure from an omitted one,
and check 6 alone would not catch it.

Check 5 (uncited assertion) is heuristic: sentence classification is not a
solved problem, so it cannot reject on its own. It flags sentences for
review instead, and its false-positive rate is measured in eval/, not
assumed low.

Check 5 was hand-adjudicated once against a full sweep: 24/24 flagged
sentences were false positives, zero fabrications, falling into three
classes — a sentence restating a rule's own `reason`/gap wording, a
sentence describing the confidence field, and a finding-summary sentence
whose actual citation lives in a neighbouring sentence (already guaranteed
present somewhere in the prose by check 7). All three are now exempted
directly: a rule id is treated as a citable source exactly like a hash or
address (enumerable straight from the Verdict, same as checks 6/7). This
is a verify-side exemption only, not a prompt instruction — an earlier
version of this fix also told the model in `narrate/prompt.py` to name
the relevant rule id, and separately to avoid timing-intensifier words
(see below). Both were reverted: measured cost was two extra prompt
constraints dropping `qwen3.5:9b-q8_0`'s first-attempt pass rate from
100% to 75% and `qwen3.5:9b`'s from 100% to 94%, on checks unrelated to
either instruction (see README's "Local model findings" for the numbers)
— instruction-following capacity is a budget the model spends, not a
free parameter, and two check-5/heuristic improvements weren't worth
that price. The exemption still fires whenever the model happens to name
a rule id unprompted; it just isn't asked to. A confidence-describing
sentence is exempted outright — it's about the confidence field, not a
transaction-level fact. Check 5 is now scoped to what it always should
have measured: a sentence asserting a specific transaction-level fact
(an amount, a counterparty, a direction, a timing) with nothing — no
hash, no address, no rule id — backing it in that sentence.

Timing-intensifier language ("immediately", "instantly", "straight away")
near `rapid_pass_through` evidence is flagged the same way, advisory only
— the pass-through window is ~20 minutes (100 blocks,
`PASS_THROUGH_WINDOW_BLOCKS` in `enrich/signals.py`), and "immediately"
overstates it, but this is prose calibration, not a fabrication, so it
never rejects.
"""

from __future__ import annotations

import re

TX_HASH_RE = re.compile(r"\b0x[0-9a-fA-F]{64}\b")
ADDRESS_RE = re.compile(r"\b0x[0-9a-fA-F]{40}\b")

# The narrative's mandated first two lines (see narrate/prompt.py's
# INSTRUCTIONS). Checks 3, 4 and 6 are exact matches against these, not
# prose search — free text below them can use words like "unassessable" or
# "flagged" in their ordinary English sense without being misread as a
# second verdict or findings statement.
HEADER_RE = re.compile(
    r"^VERDICT:\s*(FLAGGED|CLEAR|UNASSESSABLE)\s*\(confidence:\s*(low|high)\)\s*$"
)
FLAGS_LINE_RE = re.compile(r"^FLAGS:\s*(NONE|\w+(?:\s*,\s*\w+)*)\s*$")

# Verbs that describe on-chain activity: a sentence using one of these about
# the subject is making a factual claim, and a factual claim needs a citable
# hash or address in the same sentence or it's an assertion nobody can check.
ASSERTION_VERBS = (
    "sent", "sends", "sending",
    "received", "receives", "receiving",
    "transferred", "transfers", "transferring",
    "moved", "moves", "moving",
    "routed", "routes", "routing",
    "forwarded", "forwards", "forwarding",
    "interacted", "interacts", "interacting",
    "touched", "touches", "touching",
    "exposed", "exposes", "exposure",
)

SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")

CONFIDENCE_WORD_RE = re.compile(r"\bconfidence\b", re.IGNORECASE)

# Words that claim more timing precision than a rule's reason actually
# gives — see check_intensifier_language.
INTENSIFIER_RE = re.compile(r"\b(immediately|instantly|straight away|right away)\b", re.IGNORECASE)


def _walk_strings(value) -> list[str]:
    """Every string leaf value anywhere in a Case or Verdict dict — the
    literal contents of "evidence" for citation purposes. Walking the whole
    structure rather than one known field means a hash or address counts as
    cited evidence wherever in the Case it happens to live, without
    verify.py having to track every place ingest/enrich might put one.
    """
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        out = []
        for k, v in value.items():
            out.extend(_walk_strings(k))
            out.extend(_walk_strings(v))
        return out
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out.extend(_walk_strings(v))
        return out
    return []


def known_hashes(case: dict) -> set[str]:
    corpus = " ".join(_walk_strings(case))
    return {m.lower() for m in TX_HASH_RE.findall(corpus)}


def known_addresses(case: dict) -> set[str]:
    corpus = " ".join(_walk_strings(case))
    return {m.lower() for m in ADDRESS_RE.findall(corpus)}


def check_citation_existence(text: str, case: dict) -> dict:
    """Every transaction-hash-shaped token in the narrative must appear
    somewhere in the Case. Any that doesn't is a fabrication.
    """
    cited = {m.lower() for m in TX_HASH_RE.findall(text)}
    fabricated = sorted(cited - known_hashes(case))
    return {"passed": not fabricated, "fabricated_hashes": fabricated}


def check_address_existence(text: str, case: dict) -> dict:
    """Every address-shaped token in the narrative must appear somewhere
    in the Case. Any that doesn't is a fabrication.
    """
    cited = {m.lower() for m in ADDRESS_RE.findall(text)}
    fabricated = sorted(cited - known_addresses(case))
    return {"passed": not fabricated, "fabricated_addresses": fabricated}


def parse_header(text: str) -> dict | None:
    """Parses the mandated first line (see narrate/prompt.py). Returns None
    if it's missing or malformed — a missing/malformed header is itself a
    verification failure, not something checks 3/4 try to work around.
    """
    stripped = text.strip()
    if not stripped:
        return None
    first_line = stripped.splitlines()[0].strip()
    m = HEADER_RE.match(first_line)
    if not m:
        return None
    return {"verdict": m.group(1), "confidence": m.group(2)}


def parse_flags_line(text: str) -> set[str] | None:
    """Parses the mandated second line (see narrate/prompt.py). Returns
    None if it's missing or malformed — same treatment as parse_header.
    """
    lines = text.strip().splitlines()
    if len(lines) < 2:
        return None
    m = FLAGS_LINE_RE.match(lines[1].strip())
    if not m:
        return None
    body = m.group(1)
    if body == "NONE":
        return set()
    return {rule_id.strip() for rule_id in body.split(",")}


def check_verdict_fidelity(text: str, verdict: dict) -> dict:
    """Exact match of the header's verdict token against the gate's actual
    verdict — not prose search. A missing or malformed header fails this
    check outright, same as a wrong verdict would.
    """
    actual = verdict["verdict"]
    header = parse_header(text)
    stated = header["verdict"] if header else None
    return {
        "passed": stated == actual,
        "actual_verdict": actual,
        "stated_verdict": stated,
        "header_found": header is not None,
    }


def check_confidence_fidelity(text: str, verdict: dict) -> dict:
    """Exact match of the header's confidence token against the gate's
    actual confidence level. This is strictly stronger than "say so when
    it's low": the header must match exactly regardless of level, so a
    high-confidence narrative that claims low (or omits the header) fails
    just as surely as a low-confidence narrative that stays silent — the
    silent-about-thin-coverage failure this check exists to catch is one
    case of a broader exact-match requirement, not a special case of it.
    """
    confidence = verdict.get("confidence") or {}
    actual_level = confidence.get("level")
    header = parse_header(text)
    stated = header["confidence"] if header else None
    return {
        "passed": stated == actual_level,
        "actual_confidence": actual_level,
        "stated_confidence": stated,
        "header_found": header is not None,
    }


def check_finding_coverage(text: str, verdict: dict) -> dict:
    """Exact match of the FLAGS line's rule ids against the set of rules
    that actually returned FLAG in the Verdict. Unlike check 5, this can be
    exact rather than heuristic: the set of FLAG rules is enumerable
    straight from the Verdict, so there's no prose classification involved
    — a rule going unmentioned, or a rule id appearing that was never a
    FLAG, is caught by set comparison, not sentence-guessing.
    """
    actual = {r["rule"] for r in verdict["rules"] if r["outcome"] == "FLAG"}
    stated = parse_flags_line(text)
    if stated is None:
        return {"passed": False, "actual_flag_rules": sorted(actual), "stated_flag_rules": None,
                "missing": sorted(actual), "extra": [], "flags_line_found": False}
    missing = sorted(actual - stated)
    extra = sorted(stated - actual)
    return {
        "passed": stated == actual,
        "actual_flag_rules": sorted(actual),
        "stated_flag_rules": sorted(stated),
        "missing": missing,
        "extra": extra,
        "flags_line_found": True,
    }


def check_finding_substantiation(text: str, verdict: dict) -> dict:
    """Scoped per finding, not per hash: for every rule id on the FLAGS
    line that corresponds to a real rule in the Verdict, at least one of
    that rule's own evidence items must appear in the prose. One or two
    citations per finding is what makes it substantiated rather than
    merely announced — this does not require every evidence item for a
    rule with dozens of them (that's `eval/sweep.py`'s
    `_evidence_mention_rate`, a measurement, not a gate).

    A rule id on the FLAGS line with no matching rule in the Verdict at
    all (an invented id) is check 6's failure, not this one's — it's
    skipped here rather than double-counted under a different check.
    """
    stated = parse_flags_line(text)
    if stated is None:
        return {"passed": False, "unsubstantiated": None, "flags_line_found": False}

    rules_by_id = {r["rule"]: r for r in verdict["rules"]}
    prose = _strip_structural_lines(text).lower()
    unsubstantiated = []
    for rule_id in sorted(stated):
        rule = rules_by_id.get(rule_id)
        if rule is None:
            continue  # invented id — see docstring
        evidence = rule.get("evidence") or []
        if not evidence:
            continue  # nothing to cite; can't fail a citation that can't exist
        if not any(e.lower() in prose for e in evidence):
            unsubstantiated.append(rule_id)

    return {"passed": not unsubstantiated, "unsubstantiated": unsubstantiated, "flags_line_found": True}


def _strip_structural_lines(text: str) -> str:
    """Drops the mandated header/FLAGS lines before sentence-splitting, so
    a giant first "sentence" spanning structural lines and the first
    prose sentence (they don't end in '.', so SENTENCE_SPLIT_RE can't see
    the boundary) doesn't get misjudged as one long uncited claim.
    """
    lines = text.strip().splitlines()
    start = 0
    if lines and HEADER_RE.match(lines[0].strip()):
        start = 1
        if len(lines) > 1 and FLAGS_LINE_RE.match(lines[1].strip()):
            start = 2
    return "\n".join(lines[start:])


def known_rule_ids(verdict: dict) -> set[str]:
    """A rule id is a citable source exactly like a hash or address: it's
    enumerable straight from the Verdict, so a sentence that names one is
    traceable to something real, not free text. Every rule id counts here
    (not just FLAG ones) — a sentence restating an UNKNOWN rule's gap is
    exactly the case this exists for (see module docstring, exemption
    class 1).
    """
    return {r["rule"] for r in verdict["rules"]}


def check_uncited_assertion(text: str, verdict: dict) -> dict:
    """Heuristic, not exact: flags sentences that use an activity verb
    (implying a transaction-level factual claim) with nothing in the same
    sentence to back it up — no hash, no address, and no rule id. Flags
    for review rather than rejecting — see module docstring.

    Three classes of sentence are exempted outright, per the hand
    adjudication recorded in the module docstring:
    - a sentence naming a rule id (a gap/reason restatement, or a finding
      summary — check 7 already guarantees that finding's evidence is
      cited *somewhere* in the prose, so this check no longer also
      demands it in every sentence that merely announces it);
    - a sentence containing the word "confidence" (describing the
      confidence field, not a transaction fact).
    """
    flagged = []
    prose = _strip_structural_lines(text)
    rule_ids = known_rule_ids(verdict)
    for sentence in SENTENCE_SPLIT_RE.split(prose.strip()):
        if not sentence:
            continue
        lower = sentence.lower()
        has_verb = any(re.search(rf"\b{v}\b", lower) for v in ASSERTION_VERBS)
        if not has_verb:
            continue
        has_citation = bool(TX_HASH_RE.search(sentence) or ADDRESS_RE.search(sentence))
        has_rule_citation = any(re.search(rf"\b{re.escape(rid)}\b", sentence) for rid in rule_ids)
        is_confidence_sentence = bool(CONFIDENCE_WORD_RE.search(sentence))
        if not has_citation and not has_rule_citation and not is_confidence_sentence:
            flagged.append(sentence.strip())
    return {"flagged_sentences": flagged}


def check_intensifier_language(text: str, verdict: dict) -> dict:
    """Advisory, like check 5, and never rejects: flags a sentence that
    uses a timing intensifier ("immediately", "instantly", ...) while
    citing `rapid_pass_through` evidence. The rule's own reason never
    states timing that strong — the pass-through window is
    `PASS_THROUGH_WINDOW_BLOCKS` in enrich/signals.py, about 20 minutes,
    not instantaneous — so this is prose overstating a real finding
    rather than fabricating one. Surfaced for review, not enforced: see
    module docstring for why a prompt constraint aimed at this cost more
    than it was worth.
    """
    rule = next((r for r in verdict["rules"] if r["rule"] == "rapid_pass_through"), None)
    if rule is None:
        return {"flagged_sentences": []}
    evidence = rule.get("evidence") or []
    if not evidence:
        return {"flagged_sentences": []}

    flagged = []
    prose = _strip_structural_lines(text)
    for sentence in SENTENCE_SPLIT_RE.split(prose.strip()):
        if not sentence:
            continue
        if INTENSIFIER_RE.search(sentence) and any(e.lower() in sentence.lower() for e in evidence):
            flagged.append(sentence.strip())
    return {"flagged_sentences": flagged}


def verify_narrative(text: str, case: dict, verdict: dict) -> dict:
    """Runs all checks. `passed` reflects checks 1-4, 6 and 7 (exact,
    reject); check 5 and the intensifier-language flag are reported
    separately under `flags` and never affect `passed`.
    """
    checks = {
        "citation_existence": check_citation_existence(text, case),
        "address_existence": check_address_existence(text, case),
        "verdict_fidelity": check_verdict_fidelity(text, verdict),
        "confidence_fidelity": check_confidence_fidelity(text, verdict),
        "finding_coverage": check_finding_coverage(text, verdict),
        "finding_substantiation": check_finding_substantiation(text, verdict),
    }
    passed = all(c["passed"] for c in checks.values())
    return {
        "passed": passed,
        "checks": checks,
        "flags": {
            "uncited_assertion": check_uncited_assertion(text, verdict),
            "intensifier_language": check_intensifier_language(text, verdict),
        },
    }
