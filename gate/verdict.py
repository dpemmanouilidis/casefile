"""Combines rule outcomes into a Verdict. Pure function, same discipline
as gate/rules.py: no I/O, callable with a hand-written list of rule dicts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gate import rules as rules_mod

VERDICT_FLAGGED = "FLAGGED"
VERDICT_CLEAR = "CLEAR"
VERDICT_UNASSESSABLE = "UNASSESSABLE"


def compute_verdict(rule_results: list[dict], confidence: dict | None = None) -> str:
    """Any FLAG -> FLAGGED, regardless of gaps or UNKNOWNs elsewhere: a
    verified finding is not weakened by gaps elsewhere in the case — the
    same principle signals apply to individual rules, applied one level
    up to the verdict as a whole. No FLAG and any UNKNOWN -> UNASSESSABLE:
    a single UNKNOWN can still block CLEAR, but never a FLAG — this is
    fail-closed made concrete for the case as a whole, the same way it is
    for each rule.

    No FLAG and all PASS -> CLEAR, *unless* confidence is `low`, in which
    case it's UNASSESSABLE instead. This is enforced here, structurally,
    rather than by relying on every signal's own completeness to happen to
    line up with the confidence check: CLEAR-with-low-confidence is
    exactly the "claimed clean without having looked" case rule 3 exists
    to prevent, and it must be impossible regardless of which rules did or
    didn't happen to notice the thin evidence themselves.
    """
    if any(r["outcome"] == "FLAG" for r in rule_results):
        return VERDICT_FLAGGED
    if any(r["outcome"] == "UNKNOWN" for r in rule_results):
        return VERDICT_UNASSESSABLE
    if confidence is not None and confidence["level"] == "low":
        return VERDICT_UNASSESSABLE
    return VERDICT_CLEAR


def assess(case: dict, case_id: int | None = None) -> dict:
    rule_results = rules_mod.evaluate_all(case)
    confidence = rules_mod.compute_confidence(case)
    return {
        "subject": case["subject"],
        "verdict": compute_verdict(rule_results, confidence),
        "confidence": confidence,
        "rules": rule_results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id if case_id is not None else case["trace"]["trace_id"],
    }
