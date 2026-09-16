from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

from enrich import db, labels
from ingest.__main__ import load_api_key
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
