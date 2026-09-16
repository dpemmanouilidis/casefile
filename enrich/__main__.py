from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

from enrich import db, labels, trace as trace_mod
from ingest.__main__ import ingest_address, load_api_key
from ingest.evm import AlchemyClient

DEFAULT_DB_PATH = "data/casefile.db"
DEFAULT_LABELS_DIR = "enrich/labels"


def cmd_load_labels(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    db.ensure_schema(conn)

    label_dir = Path(args.dir)
    files = sorted(label_dir.glob("*.csv"))
    if not files:
        print(f"no *.csv files found under {label_dir}", file=sys.stderr)
        return 1

    for path in files:
        try:
            written, replaced = labels.load_labels_into_db(conn, path)
        except labels.MalformedLabelFile as exc:
            print(f"REJECTED {path}: {exc}", file=sys.stderr)
            return 1
        print(f"  {path}: {written} label(s) loaded ({replaced} prior row(s) from this file's source(s) replaced)")

    coverage = db.label_coverage(conn)
    print(f"label coverage: {coverage['labelled']}/{coverage['total_addresses']} addresses labelled, {coverage['unknown']} unknown")
    conn.close()
    return 0


def cmd_compute_is_contract(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    db.ensure_schema(conn)

    api_key = load_api_key()
    client = AlchemyClient(api_key=api_key)

    targets = db.addresses_missing_is_contract(conn)
    print(f"{len(targets)} address(es) need is_contract computed (1 RPC call each)")
    rpc_calls = 0
    contracts = 0
    for address in targets:
        is_contract = client.is_contract(address)
        rpc_calls += 1
        contracts += int(is_contract)
        db.set_is_contract(conn, address, is_contract)
    conn.commit()

    print(f"done: {rpc_calls} eth_getCode call(s) made, {contracts} address(es) are contracts")
    conn.close()
    return 0


def cmd_trace(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    db.ensure_schema(conn)

    min_value = int(args.min_value) if args.min_value is not None else None
    try:
        trace_id = trace_mod.run_trace(
            conn,
            chain_id=args.chain,
            subject=args.address,
            hops=args.hops,
            direction=args.direction,
            min_value=min_value,
            max_fanout=args.max_fanout,
        )
    except ValueError as exc:
        print(f"invalid trace parameters: {exc}", file=sys.stderr)
        return 1
    run = db.get_trace_run(conn, trace_id)
    edges = db.get_trace_edges(conn, trace_id)
    terminal_counts: dict[str, int] = defaultdict(int)
    for e in edges:
        if e["terminal_reason"]:
            terminal_counts[e["terminal_reason"]] += 1
    print(f"trace #{trace_id}: {len(edges)} edge(s) recorded, status={run['status']}")
    for reason, count in sorted(terminal_counts.items()):
        print(f"  {reason}: {count} edge(s)")
    if run["note"]:
        print(f"  note: {run['note']}")
    conn.close()
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    run = db.get_trace_run(conn, args.trace_id)
    if run is None:
        print(f"no trace with id {args.trace_id}", file=sys.stderr)
        return 1
    edges = db.get_trace_edges(conn, args.trace_id)

    if args.format == "json":
        payload = dict(run)
        payload["trace_id"] = args.trace_id
        payload["edges"] = []
        for e in edges:
            edge = dict(e)
            edge["from_label"] = db.label_for(conn, run["chain_id"], e["from_address"])
            edge["to_label"] = db.label_for(conn, run["chain_id"], e["to_address"])
            payload["edges"].append(edge)
        print(json.dumps(payload, indent=2))
    else:
        print(f"trace #{args.trace_id}: subject={run['subject']} hops={run['hops']} direction={run['direction']} status={run['status']}")
        if run["note"]:
            print(f"  note: {run['note']}")
        for e in edges:
            from_label = db.label_for(conn, run["chain_id"], e["from_address"])
            to_label = db.label_for(conn, run["chain_id"], e["to_address"])
            reason = f"  [{e['terminal_reason']}]" if e["terminal_reason"] else ""
            print(
                f"  hop {e['hop']}: {e['from_address']} ({from_label}) -> "
                f"{e['to_address']} ({to_label}) amount_raw={e['amount_raw']} tx={e['tx_hash']}{reason}"
            )
    conn.close()
    return 0


def cmd_expand(args: argparse.Namespace) -> int:
    conn = sqlite3.connect(args.db)
    db.ensure_schema(conn)

    address = args.address.lower()
    rows = conn.execute(
        """
        SELECT from_address, to_address, amount_raw FROM transfers
        WHERE chain_id = ? AND asset_address IS NULL AND (from_address = ? OR to_address = ?)
        """,
        (args.chain, address, address),
    ).fetchall()

    totals: dict[str, int] = defaultdict(int)
    for from_a, to_a, amount_raw in rows:
        counterparty = to_a if from_a == address else from_a
        if counterparty == address:
            continue
        totals[counterparty] += int(amount_raw)

    candidates = []
    for counterparty, total in totals.items():
        if trace_mod._node_categories(conn, args.chain, counterparty) & trace_mod.CUSTODY_CATEGORIES:
            continue  # never expand exchange/mixer nodes
        candidates.append((counterparty, total))
    candidates.sort(key=lambda pair: -pair[1])
    top = candidates[: args.top]

    print(f"expand: {len(top)} address(es) will be ingested (--top {args.top}, exchange/mixer excluded, ranked by native ETH value)")
    for counterparty, total in top:
        print(f"  {counterparty}: {total / 1e18:.6f} ETH total")

    if not top:
        conn.close()
        return 0

    api_key = load_api_key()
    client = AlchemyClient(api_key=api_key)
    latest = client.latest_block()
    from_block = max(0, latest - args.blocks)

    for counterparty, _total in top:
        ingest_address(conn, client, counterparty, from_block, latest, dry_run=False)

    conn.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m enrich")
    sub = parser.add_subparsers(dest="command", required=True)

    p_load = sub.add_parser("load-labels", help="load enrich/labels/*.csv into the labels table")
    p_load.add_argument("--dir", default=DEFAULT_LABELS_DIR)
    p_load.add_argument("--db", default=DEFAULT_DB_PATH)
    p_load.set_defaults(func=cmd_load_labels)

    p_contract = sub.add_parser("compute-is-contract", help="set addresses.is_contract via eth_getCode")
    p_contract.add_argument("--db", default=DEFAULT_DB_PATH)
    p_contract.set_defaults(func=cmd_compute_is_contract)

    p_trace = sub.add_parser("trace", help="offline breadth-first trace over the local transfers table")
    p_trace.add_argument("--chain", required=True, choices=["ethereum"])
    p_trace.add_argument("--address", required=True)
    p_trace.add_argument("--hops", type=int, required=True)
    p_trace.add_argument("--direction", choices=sorted(trace_mod.VALID_DIRECTIONS), default="out")
    p_trace.add_argument("--min-value", default=None, help="raw base-unit threshold; not normalised across assets")
    p_trace.add_argument("--max-fanout", type=int, default=trace_mod.DEFAULT_MAX_FANOUT)
    p_trace.add_argument("--db", default=DEFAULT_DB_PATH)
    p_trace.set_defaults(func=cmd_trace)

    p_show = sub.add_parser("show", help="print a trace's subgraph")
    p_show.add_argument("--trace-id", type=int, required=True)
    p_show.add_argument("--format", choices=["text", "json"], default="text")
    p_show.add_argument("--db", default=DEFAULT_DB_PATH)
    p_show.set_defaults(func=cmd_show)

    p_expand = sub.add_parser("expand", help="ingest a subject's top-N direct counterparties by ETH value")
    p_expand.add_argument("--chain", required=True, choices=["ethereum"])
    p_expand.add_argument("--address", required=True)
    p_expand.add_argument("--top", type=int, default=20)
    p_expand.add_argument("--blocks", type=int, default=5000)
    p_expand.add_argument("--db", default=DEFAULT_DB_PATH)
    p_expand.set_defaults(func=cmd_expand)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
