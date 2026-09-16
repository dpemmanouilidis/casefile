"""Combines rule outcomes into a Verdict. Pure function, same discipline
as gate/rules.py: no I/O, callable with a hand-written list of rule dicts.
"""

from __future__ import annotations

from datetime import datetime, timezone

from gate import rules as rules_mod

VERDICT_FLAGGED = "FLAGGED"
VERDICT_CLEAR = "CLEAR"
VERDICT_UNASSESSABLE = "UNASSESSABLE"


def compute_verdict(rule_results: list[dict]) -> str:
    """CLEAR requires every rule to return PASS. A single UNKNOWN makes
    the verdict UNASSESSABLE, never CLEAR — this is fail-closed made
    concrete. The `unassessable` rule firing FLAG maps to the
    UNASSESSABLE verdict specifically, not FLAGGED: "the evidence is too
    thin to judge" must never read as "the subject did something".
    """
    by_rule = {r["rule"]: r for r in rule_results}

    unassessable_result = by_rule.get("unassessable")
    if unassessable_result is not None and unassessable_result["outcome"] == "FLAG":
        return VERDICT_UNASSESSABLE

    if any(r["outcome"] == "UNKNOWN" for r in rule_results):
        return VERDICT_UNASSESSABLE

    if any(r["outcome"] == "FLAG" for r in rule_results if r["rule"] != "unassessable"):
        return VERDICT_FLAGGED

    return VERDICT_CLEAR


def assess(case: dict, case_id: int | None = None) -> dict:
    rule_results = rules_mod.evaluate_all(case)
    return {
        "subject": case["subject"],
        "verdict": compute_verdict(rule_results),
        "rules": rule_results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "case_id": case_id if case_id is not None else case["trace"]["trace_id"],
    }
