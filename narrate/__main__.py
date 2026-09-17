from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from gate import db as gate_db
from narrate import db as narrate_db
from narrate import model as model_mod
from narrate import verify as verify_mod

DEFAULT_DB_PATH = "data/casefile.db"
DEFAULT_MODEL = "qwen3.5:9b"
DEFAULT_QUANTIZATION = "Q4_K_M"


def _print_result(result: dict, verdict_id: int, narrative_id: int, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps({"narrative_id": narrative_id, "verdict_id": verdict_id, **result}, indent=2))
        return
    if fmt == "markdown":
        print(f"# Narrative #{narrative_id} (verdict #{verdict_id})\n")
        print(f"**Source:** {result['source']}  ")
        print(f"**Model:** {result['model']} ({result['quantization']}, think={result['think']})  ")
        print(f"**Attempts:** {len(result['attempts'])}, successful: {result['successful_attempt']}\n")
        print(result["text"])
        return
    # text
    print(f"narrative #{narrative_id} for verdict #{verdict_id}: source={result['source']} model={result['model']}"
          f" ({result['quantization']}, think={result['think']})")
    print(f"  attempts={len(result['attempts'])} successful_attempt={result['successful_attempt']}")
    for a in result["attempts"]:
        v = a["verification"]
        status = "PASSED" if v["passed"] else "failed: " + ", ".join(k for k, c in v["checks"].items() if not c["passed"])
        print(f"  attempt {a['attempt']} (seed={a['seed']}): {status}")
    print()
    print(result["text"])


def cmd_write(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    gate_db.ensure_schema(conn)
    narrate_db.ensure_schema(conn)

    stored_verdict = gate_db.load_verdict(conn, args.verdict_id)
    if stored_verdict is None:
        print(f"no verdict with id {args.verdict_id}", file=sys.stderr)
        return 1

    case = stored_verdict["case"]
    verdict = {
        "subject": stored_verdict["subject"],
        "verdict": stored_verdict["verdict"],
        "confidence": stored_verdict["confidence"],
        "rules": stored_verdict["rules"],
        "case_id": stored_verdict["case_id"],
    }

    try:
        result = model_mod.narrate_verdict(
            case, verdict, args.verdict_id, args.model, args.quantization,
            think=args.think, max_attempts=args.max_attempts, base_seed=args.base_seed, num_ctx=args.num_ctx,
        )
    except model_mod.ModelUnavailable as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    narrative_id = narrate_db.save_narrative(conn, args.verdict_id, result)
    _print_result(result, args.verdict_id, narrative_id, args.format)

    conn.close()
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Re-runs narrate/verify.py's checks on a stored narrative, against
    the verdict it was written for. Does not call the model — this is the
    deterministic half only, exactly as narrate/verify.py's own docstring
    promises: callable with nothing but the stored strings and dicts.
    """
    conn = sqlite3.connect(args.db)
    gate_db.ensure_schema(conn)
    narrate_db.ensure_schema(conn)

    narrative = narrate_db.load_narrative(conn, args.narrative_id)
    if narrative is None:
        print(f"no narrative with id {args.narrative_id}", file=sys.stderr)
        return 1

    stored_verdict = gate_db.load_verdict(conn, narrative["verdict_id"])
    if stored_verdict is None:
        print(f"narrative #{args.narrative_id} references missing verdict #{narrative['verdict_id']}", file=sys.stderr)
        return 1

    case = stored_verdict["case"]
    verdict = {
        "verdict": stored_verdict["verdict"],
        "confidence": stored_verdict["confidence"],
        "rules": stored_verdict["rules"],
    }

    result = verify_mod.verify_narrative(narrative["text"], case, verdict)

    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"narrative #{args.narrative_id} (verdict #{narrative['verdict_id']}, source={narrative['source']})")
        print(f"  passed: {result['passed']}")
        for name, check in result["checks"].items():
            print(f"    [{'OK' if check['passed'] else 'FAIL'}] {name}")
        n_flagged = len(result["flags"]["uncited_assertion"]["flagged_sentences"])
        print(f"  check-5 flags: {n_flagged}")

    conn.close()
    return 0 if result["passed"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m narrate")
    sub = parser.add_subparsers(dest="command", required=True)

    p_write = sub.add_parser("write", help="generate (and verify) a narrative for a stored verdict")
    p_write.add_argument("--verdict-id", type=int, required=True)
    p_write.add_argument("--model", default=DEFAULT_MODEL)
    p_write.add_argument("--quantization", default=DEFAULT_QUANTIZATION)
    p_write.add_argument("--think", action="store_true")
    p_write.add_argument("--max-attempts", type=int, default=model_mod.MAX_NARRATION_ATTEMPTS)
    p_write.add_argument("--base-seed", type=int, default=model_mod.DEFAULT_SEED)
    p_write.add_argument("--num-ctx", type=int, default=model_mod.DEFAULT_NUM_CTX)
    p_write.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    p_write.add_argument("--db", default=DEFAULT_DB_PATH)
    p_write.set_defaults(func=cmd_write)

    p_verify = sub.add_parser("verify", help="re-run checks on a stored narrative")
    p_verify.add_argument("--narrative-id", type=int, required=True)
    p_verify.add_argument("--format", choices=["text", "json"], default="text")
    p_verify.add_argument("--db", default=DEFAULT_DB_PATH)
    p_verify.set_defaults(func=cmd_verify)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
