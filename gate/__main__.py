from __future__ import annotations

import argparse
import json
import sqlite3
import sys

from enrich import case as case_mod
from enrich import db as enrich_db
from enrich import trace as trace_mod
from gate import db as gate_db
from gate import verdict as verdict_mod

DEFAULT_DB_PATH = "data/casefile.db"
DEFAULT_HOPS = 2


def _print_verdict(verdict: dict, fmt: str) -> None:
    if fmt == "json":
        print(json.dumps(verdict, indent=2))
        return
    confidence = verdict["confidence"]
    print(f"subject={verdict['subject']} verdict={verdict['verdict']} confidence={confidence['level']} case_id={verdict['case_id']}")
    print(f"  confidence reason: {confidence['reason']}")
    for r in verdict["rules"]:
        print(f"  [{r['outcome']:7}] {r['rule']}: {r['reason']}")
        if r["evidence"]:
            print(f"            evidence: {', '.join(r['evidence'][:5])}" + (" ..." if len(r["evidence"]) > 5 else ""))


def cmd_assess(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    enrich_db.ensure_schema(conn)
    gate_db.ensure_schema(conn)

    case = case_mod.build_case(
        conn, args.chain, args.address, hops=args.hops, direction="both", max_fanout=args.max_fanout
    )
    verdict = verdict_mod.assess(case)
    verdict_id = gate_db.save_verdict(conn, args.chain, case, verdict)
    print(f"verdict #{verdict_id} stored (case_id={verdict['case_id']})")
    _print_verdict(verdict, args.format)

    conn.close()
    return 0


def cmd_replay(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    gate_db.ensure_schema(conn)

    stored = gate_db.load_verdict(conn, args.verdict_id)
    if stored is None:
        print(f"no verdict with id {args.verdict_id}", file=sys.stderr)
        return 1

    recomputed = verdict_mod.assess(stored["case"], case_id=stored["case_id"])

    rules_match = recomputed["rules"] == stored["rules"]
    confidence_match = recomputed["confidence"] == stored["confidence"]
    verdict_match = recomputed["verdict"] == stored["verdict"]
    identical = rules_match and confidence_match and verdict_match

    print(f"verdict #{args.verdict_id}: stored={stored['verdict']} recomputed={recomputed['verdict']}")
    print(f"  stored confidence={stored['confidence']['level']} recomputed confidence={recomputed['confidence']['level']}")
    print(f"rules identical: {rules_match}, confidence identical: {confidence_match}")
    print("REPRODUCED" if identical else "MISMATCH — not reproducible from stored evidence")

    conn.close()
    return 0 if identical else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gate")
    sub = parser.add_subparsers(dest="command", required=True)

    p_assess = sub.add_parser("assess", help="build a case and compute a verdict")
    p_assess.add_argument("--chain", required=True, choices=["ethereum"])
    p_assess.add_argument("--address", required=True)
    p_assess.add_argument("--hops", type=int, default=DEFAULT_HOPS)
    p_assess.add_argument("--max-fanout", type=int, default=trace_mod.DEFAULT_MAX_FANOUT)
    p_assess.add_argument("--format", choices=["text", "json"], default="text")
    p_assess.add_argument("--db", default=DEFAULT_DB_PATH)
    p_assess.set_defaults(func=cmd_assess)

    p_replay = sub.add_parser("replay", help="recompute a stored verdict from its stored case and assert identical")
    p_replay.add_argument("--verdict-id", type=int, required=True)
    p_replay.add_argument("--db", default=DEFAULT_DB_PATH)
    p_replay.set_defaults(func=cmd_replay)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
