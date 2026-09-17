"""Runs narration across every stored verdict, per model/quantisation/think
configuration, and reports the numbers that go in the README table.

This drives the exact same code path `python -m narrate write` uses
(narrate.model.narrate_verdict + narrate.db.save_narrative) — it is an
orchestration loop over verdicts and configs, not a second implementation
of generation or verification.

Verdicts are read from the project's real database (`data/casefile.db`,
read-only from here), but every narrative this script produces is written
to its own database (`eval/sweep.db`), not the project database's
`narratives` table. A sweep run clears and repopulates `eval/sweep.db`
every time — it's disposable, regenerable measurement output, not
storage. That separation exists because it wasn't always there: three
times, a README section cited a specific narrative id from a hand-run
`python -m narrate write`, and a later sweep's own narration runs
(against the same `narratives` table, cleared between sweeps for a clean
comparison) deleted that exact row out from under the citation. A
narrative worth citing in the README is written by hand with
`python -m narrate write` against the real database and never touched by
`eval/sweep.py` again — inspect a sweep's own narratives with
`python -m narrate verify --db eval/sweep.db --narrative-id N`.

Check 5 (uncited assertion) is heuristic and its false-positive rate is not
estimated here — every flagged sentence from the sweep is dumped to
eval/check5_flags_for_review.txt for hand adjudication (see
docs/milestone-4.md, acceptance criterion on check 5). The intensifier-
language flag (advisory, see narrate/verify.py's check_intensifier_language)
gets the same treatment in eval/intensifier_flags_for_review.txt.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import time
from pathlib import Path

from gate import db as gate_db
from narrate import db as narrate_db
from narrate import model as model_mod

DB_PATH = "data/casefile.db"
SWEEP_DB_PATH = Path(__file__).parent / "sweep.db"
RESULTS_JSON = Path(__file__).parent / "sweep_results.json"
RESULTS_MD = Path(__file__).parent / "sweep_results.md"
CHECK5_DUMP = Path(__file__).parent / "check5_flags_for_review.txt"
INTENSIFIER_DUMP = Path(__file__).parent / "intensifier_flags_for_review.txt"

ROWS = [
    {"label": "qwen3.5:9b Q4_K_M think:false", "model": "qwen3.5:9b", "quantization": "Q4_K_M", "think": False},
    {"label": "qwen3.5:9b-q8_0 Q8_0 think:false", "model": "qwen3.5:9b-q8_0", "quantization": "Q8_0", "think": False},
    {"label": "qwen2.5:3b Q4_K_M think:false", "model": "qwen2.5:3b", "quantization": "Q4_K_M", "think": False},
    {"label": "qwen3.5:9b Q4_K_M think:true", "model": "qwen3.5:9b", "quantization": "Q4_K_M", "think": True},
]


def _vram_used_mib() -> int | None:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"], timeout=10
        )
        return int(out.decode().strip().splitlines()[0])
    except Exception:
        return None


def _flag_finding_evidence(verdict: dict) -> list[str]:
    hashes = []
    for r in verdict["rules"]:
        if r["outcome"] == "FLAG":
            hashes.extend(r["evidence"])
    return hashes


def _evidence_mention_rate(text: str, verdict: dict) -> float | None:
    """Of every individual evidence hash belonging to a FLAG rule, what
    fraction appears as a literal substring anywhere in the final
    narrative's prose? This is NOT what narrate/verify.py's check 6
    (`check_finding_coverage`) enforces — check 6 is an exact match of
    rule ids on the structural `FLAGS:` line, decoupled from prose by
    design (see narrate/prompt.py). A narrative can satisfy check 6
    (every FLAG rule id listed) while mentioning few of that rule's
    individual evidence hashes in the prose below, or vice versa if it
    somehow lost the header. The two numbers can and do disagree in the
    sweep results — that's real, not a bug in either one — which is why
    this function has its own name instead of borrowing "finding
    coverage" from docs/milestone-4.md's original spec.
    """
    evidence = _flag_finding_evidence(verdict)
    if not evidence:
        return None  # nothing to cover — excluded from the row average, not counted as 0
    lower = text.lower()
    cited = sum(1 for h in evidence if h.lower() in lower)
    return cited / len(evidence)


def run_row(sweep_conn: sqlite3.Connection, row: dict, verdict_rows: list[tuple]) -> dict:
    from narrate import verify as verify_mod

    per_verdict = []
    vram_before = _vram_used_mib()
    row_wall_start = time.time()

    for verdict_id, subject, vname, case_json, rules_json, confidence_json in verdict_rows:
        case = json.loads(case_json)
        verdict = {
            "verdict": vname,
            "rules": json.loads(rules_json),
            "confidence": json.loads(confidence_json),
        }

        t0 = time.time()
        try:
            result = model_mod.narrate_verdict(
                case, verdict, verdict_id, row["model"], row["quantization"],
                think=row["think"], num_ctx=model_mod.DEFAULT_NUM_CTX,
            )
        except model_mod.ModelUnavailable as exc:
            # Observed in practice: switching rows to a model that barely
            # fits (e.g. qwen3.5:9b-q8_0, ~300MB headroom on 12GB — see
            # README) can 500 while Ollama is still unloading the previous
            # row's model. One retry after a short wait, not a silent skip
            # — a verdict dropped from the denominator would inflate this
            # row's pass rate rather than report the failure.
            print(f"  verdict {verdict_id}: MODEL UNAVAILABLE, retrying once in 5s: {exc}")
            time.sleep(5)
            try:
                result = model_mod.narrate_verdict(
                    case, verdict, verdict_id, row["model"], row["quantization"],
                    think=row["think"], num_ctx=model_mod.DEFAULT_NUM_CTX,
                )
            except model_mod.ModelUnavailable as exc2:
                print(f"  verdict {verdict_id}: MODEL UNAVAILABLE on retry too, skipping: {exc2}")
                continue
        wall = time.time() - t0

        narrative_id = narrate_db.save_narrative(sweep_conn, verdict_id, result)

        cited_hashes = {m.lower() for m in verify_mod.TX_HASH_RE.findall(result["text"])}
        known = verify_mod.known_hashes(case)
        fabricated = cited_hashes - known
        mention_rate = _evidence_mention_rate(result["text"], verdict)

        check5_flags = []
        intensifier_flags = []
        check6_passed = None
        if result["verification"] is not None:
            check5_flags = result["verification"]["flags"]["uncited_assertion"]["flagged_sentences"]
            intensifier_flags = result["verification"]["flags"]["intensifier_language"]["flagged_sentences"]
            check6_passed = result["verification"]["checks"]["finding_coverage"]["passed"]

        # Which check(s) actually blocked each *failed* attempt — not just
        # whether the final narrative passed. This is what explains a
        # fallback: "check6 passed 100% of the time" is trivially true if
        # it only counts narratives that already succeeded on everything.
        failed_checks = sorted({
            name
            for a in result["attempts"]
            if not a["verification"]["passed"]
            for name, c in a["verification"]["checks"].items()
            if not c["passed"]
        })

        per_verdict.append({
            "verdict_id": verdict_id,
            "narrative_id": narrative_id,
            "subject": subject,
            "source": result["source"],
            "first_attempt_passed": result["attempts"][0]["verification"]["passed"] if result["attempts"] else None,
            "passed_after_retries": result["source"] == "model",
            "n_attempts": len(result["attempts"]),
            "wall_seconds": wall,
            "cited_hashes": len(cited_hashes),
            "fabricated_hashes": len(fabricated),
            "evidence_mention_rate": mention_rate,
            "check6_finding_coverage_passed": check6_passed,
            "failed_checks": failed_checks,
            "check5_flags": check5_flags,
            "intensifier_flags": intensifier_flags,
        })
        print(f"  verdict {verdict_id}: source={result['source']} attempts={len(result['attempts'])} "
              f"wall={wall:.1f}s evidence_mention_rate={mention_rate} check6={check6_passed} "
              f"failed_checks={failed_checks}")

    vram_after = _vram_used_mib()
    row_wall = time.time() - row_wall_start

    n = len(per_verdict)
    citation_precision = None
    total_cited = sum(v["cited_hashes"] for v in per_verdict)
    total_fabricated = sum(v["fabricated_hashes"] for v in per_verdict)
    if total_cited:
        citation_precision = (total_cited - total_fabricated) / total_cited

    mention_rates = [v["evidence_mention_rate"] for v in per_verdict if v["evidence_mention_rate"] is not None]
    check6_results = [v["check6_finding_coverage_passed"] for v in per_verdict if v["check6_finding_coverage_passed"] is not None]

    # How many verdicts had each check among their failure reasons, across
    # every failed attempt — this is what actually explains a fallback
    # rate, unlike a pass-rate column that only sees narratives that
    # eventually succeeded on everything.
    rejection_reasons: dict[str, int] = {}
    for v in per_verdict:
        for name in v["failed_checks"]:
            rejection_reasons[name] = rejection_reasons.get(name, 0) + 1

    return {
        "label": row["label"],
        "model": row["model"],
        "quantization": row["quantization"],
        "think": row["think"],
        "num_ctx": model_mod.DEFAULT_NUM_CTX,
        "n_verdicts": n,
        "citation_precision": citation_precision,
        "first_attempt_pass_rate": sum(1 for v in per_verdict if v["first_attempt_passed"]) / n if n else None,
        "post_retry_pass_rate": sum(1 for v in per_verdict if v["passed_after_retries"]) / n if n else None,
        "fallback_rate": sum(1 for v in per_verdict if v["source"] == "template") / n if n else None,
        "evidence_mention_rate_mean": sum(mention_rates) / len(mention_rates) if mention_rates else None,
        "check6_pass_rate": sum(1 for c in check6_results if c) / len(check6_results) if check6_results else None,
        "rejection_reasons": rejection_reasons,
        "mean_attempts": sum(v["n_attempts"] for v in per_verdict) / n if n else None,
        "wall_seconds_total": row_wall,
        "vram_before_mib": vram_before,
        "vram_after_mib": vram_after,
        "per_verdict": per_verdict,
    }


def main() -> int:
    # Read-only: verdicts come from the real project database. This
    # connection never writes a narrative — see module docstring for why.
    conn = sqlite3.connect(DB_PATH)
    gate_db.ensure_schema(conn)

    # Write-only for narrations: the sweep's own disposable database,
    # cleared at the start of every run for a clean comparison. Nothing a
    # reader might cite from the README ever lives here.
    sweep_conn = sqlite3.connect(SWEEP_DB_PATH)
    narrate_db.ensure_schema(sweep_conn)
    sweep_conn.execute("DELETE FROM narratives")
    sweep_conn.commit()

    all_verdict_rows = conn.execute(
        "SELECT id, subject, verdict, case_json, rules_json, confidence_json FROM verdicts ORDER BY id"
    ).fetchall()

    # Verdicts 1-6 once predated confidence tracking and didn't reproduce
    # under current gate/rules.py (see README's "Incident" section) — that
    # was fixed by recomputing them in place, not by excluding them here.
    # This filter is now just a defensive fail-closed check: any verdict
    # still missing confidence, now or in the future, is a `gate replay`
    # data-integrity gap and does not belong in a narration sweep.
    verdict_rows = [r for r in all_verdict_rows if json.loads(r[5]).get("level") is not None]
    excluded = [r[0] for r in all_verdict_rows if r not in verdict_rows]
    if excluded:
        print(f"excluding {len(excluded)} verdict(s) with no recorded confidence (ids {excluded}) — "
              f"this should not happen; see the comment above `verdict_rows` in this file")
    print(f"sweeping {len(verdict_rows)} stored verdicts across {len(ROWS)} configurations")

    all_results = []
    for row in ROWS:
        print(f"\n=== {row['label']} ===")
        result = run_row(sweep_conn, row, verdict_rows)
        all_results.append(result)
        if result["rejection_reasons"]:
            print(f"  rejection reasons (verdicts with >=1 failed attempt citing this check): {result['rejection_reasons']}")

    RESULTS_JSON.write_text(json.dumps(all_results, indent=2), encoding="utf-8")

    with open(CHECK5_DUMP, "w", encoding="utf-8") as f:
        f.write("Check 5 (uncited assertion) flagged sentences — hand-adjudicate true/false positive.\n")
        f.write("Format: [row] narrative_id (verdict_id): sentence\n\n")
        for r in all_results:
            for v in r["per_verdict"]:
                for s in v["check5_flags"]:
                    f.write(f"[{r['label']}] narrative_id={v['narrative_id']} verdict_id={v['verdict_id']}: {s}\n")

    with open(INTENSIFIER_DUMP, "w", encoding="utf-8") as f:
        f.write("Intensifier-language flags (advisory, never rejects) — review only.\n")
        f.write("Format: [row] narrative_id (verdict_id): sentence\n\n")
        for r in all_results:
            for v in r["per_verdict"]:
                for s in v["intensifier_flags"]:
                    f.write(f"[{r['label']}] narrative_id={v['narrative_id']} verdict_id={v['verdict_id']}: {s}\n")

    _write_markdown_table(all_results)
    conn.close()
    sweep_conn.close()
    print(f"\nwrote {RESULTS_JSON}, {RESULTS_MD}, {CHECK5_DUMP}, {INTENSIFIER_DUMP}")
    print(f"narrations written to {SWEEP_DB_PATH} (disposable — never cite one of these ids from the README)")
    return 0


def _write_markdown_table(all_results: list[dict]) -> None:
    lines = [
        "| Row | n | Citation precision | 1st-attempt pass | Post-retry pass | Fallback rate | Check 6 pass rate | Evidence mention rate | Mean attempts | Wall (s) | VRAM (MiB) | num_ctx |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in all_results:
        def pct(x):
            return f"{x * 100:.0f}%" if x is not None else "n/a"

        vram = r["vram_after_mib"] if r["vram_after_mib"] is not None else "n/a"
        lines.append(
            f"| {r['label']} | {r['n_verdicts']} | {pct(r['citation_precision'])} | {pct(r['first_attempt_pass_rate'])} | "
            f"{pct(r['post_retry_pass_rate'])} | {pct(r['fallback_rate'])} | {pct(r['check6_pass_rate'])} | "
            f"{pct(r['evidence_mention_rate_mean'])} | "
            f"{r['mean_attempts']:.1f} | {r['wall_seconds_total']:.0f} | {vram} | {r['num_ctx']} |"
        )
    RESULTS_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
