"""Builds the evidence bundle handed to the model, and the prompt text
around it.

Correction to the original milestone spec: the prompt does not carry the
whole Case. A subject with dozens of hop-1 counterparties produces a trace
that does not fit in any context size this card can run — see the
token-count measurement recorded in eval/. Instead the bundle below is a
strict subset of the Case, built entirely from the Verdict already computed
by gate/:

- the verdict and its confidence (with the figures behind the level, not
  just the word)
- every rule that returned FLAG or UNKNOWN, with its reason and evidence
- the gaps behind each UNKNOWN, via the rule's reason (gate/rules.py always
  states the gap in the reason string — see gate/rules.py)

A rule that returned PASS asserts nothing happened and carries no evidence
worth restating, so it contributes nothing to the bundle. Every fact placed
in the bundle is still traceable straight back to gate/'s own output, so
narrate/verify.py's citation-existence check still holds against it exactly
as it would against the full Case — the bundle is a subset, never a
paraphrase, of the Case's evidence.
"""

from __future__ import annotations

RELEVANT_OUTCOMES = ("FLAG", "UNKNOWN")

INSTRUCTIONS = """You restate findings that have already been determined. You do not
decide, weigh, or add anything you know from training about addresses, labels, or
categories such as mixers or sanctions programs. Every factual sentence about the
subject's activity must cite a transaction hash or address from the evidence below —
if you cannot cite it, do not state it.

Your first two lines must be exactly this, with no other text before them:
VERDICT: <FLAGGED|CLEAR|UNASSESSABLE> (confidence: <low|high>)
FLAGS: <comma-separated rule ids of every finding below marked [FLAG], or NONE if there are none>
Fill in the verdict and confidence given below, verbatim, and list every [FLAG] rule id
from Findings below by its exact id — omitting one is a failure, and so is inventing an
id that isn't in Findings. After these two lines, write the narrative as prose. For
every rule id on the FLAGS line, cite at least one of that rule's own evidence hashes
somewhere in the prose — one or two citations per finding is enough, you do not need to
list every hash a rule has. An announced finding with no citation for it is a failure.
There you may use words like "unassessable" or "flagged" in their ordinary English
sense without them being read as a second verdict or findings statement — only the
first two lines are checked structurally."""


def build_bundle(case: dict, verdict: dict) -> dict:
    findings = [
        {
            "rule": r["rule"],
            "outcome": r["outcome"],
            "reason": r["reason"],
            "evidence": r["evidence"],
        }
        for r in verdict["rules"]
        if r["outcome"] in RELEVANT_OUTCOMES
    ]
    return {
        "subject": case["subject"],
        "chain_id": case["chain_id"],
        "verdict": verdict["verdict"],
        "confidence": verdict["confidence"],
        "findings": findings,
    }


def _render_finding(f: dict) -> str:
    evidence = ", ".join(f["evidence"]) if f["evidence"] else "(none)"
    return f"- [{f['outcome']}] {f['rule']}: {f['reason']}\n  evidence: {evidence}"


def render_prompt(bundle: dict) -> str:
    confidence = bundle["confidence"]
    # Fail closed: a verdict missing its confidence record is itself a gap,
    # never treated as "confidence unstated therefore assume high".
    level = confidence.get("level", "unknown (confidence not recorded for this verdict)")
    findings_text = "\n".join(_render_finding(f) for f in bundle["findings"]) or "(no FLAG or UNKNOWN findings)"
    return f"""{INSTRUCTIONS}

Subject: {bundle['subject']} ({bundle['chain_id']})
Verdict: {bundle['verdict']}
Confidence: {level} — {confidence.get('reason', '')}

Findings:
{findings_text}

Write the narrative now."""


def build_prompt(case: dict, verdict: dict) -> str:
    return render_prompt(build_bundle(case, verdict))
