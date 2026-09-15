from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ingest import db
from ingest.evm import AlchemyClient, RateLimitExceeded, RpcError

DEFAULT_DB_PATH = "data/casefile.db"
DEFAULT_BLOCKS = 5000  # keeps free-tier RPC usage bounded; see addresses.txt for why


def load_addresses(path: str) -> list[str]:
    addresses = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                addresses.append(line.lower())
    return addresses


def load_api_key() -> str:
    key = os.environ.get("ALCHEMY_API_KEY")
    if not key:
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("ALCHEMY_API_KEY="):
                    key = line.split("=", 1)[1].strip()
                    break
    if not key:
        raise SystemExit("ALCHEMY_API_KEY not set in environment or .env")
    return key


def ingest_address(
    conn,
    client: AlchemyClient,
    address: str,
    from_block: int,
    to_block: int,
    dry_run: bool,
) -> tuple[str, int, str | None]:
    """Returns (status, tx_count, note)."""
    run_id = None if dry_run else db.start_run(conn, client.chain_id, address, from_block, to_block)
    tx_count = 0
    try:
        transactions = list(client.transactions(address, from_block, to_block))
        transfers = list(client.transfers(address, from_block, to_block))
    except (RateLimitExceeded, RpcError) as exc:
        note = str(exc)
        if run_id is not None:
            db.finish_run(conn, run_id, "partial", tx_count, note)
        return "partial", tx_count, note
    except KeyboardInterrupt:
        note = "interrupted by user"
        if run_id is not None:
            db.finish_run(conn, run_id, "partial", tx_count, note)
        raise

    tx_count = len(transactions)
    if dry_run:
        print(f"  {address}: {len(transactions)} transactions, {len(transfers)} transfers (dry-run, nothing written)")
        return "ok", tx_count, None

    db.upsert_address(conn, client.chain_id, address, is_subject=True)
    for tx in transactions:
        db.insert_transaction(conn, tx)
        db.upsert_address(conn, client.chain_id, tx.from_address, is_subject=False)
        if tx.to_address:
            db.upsert_address(conn, client.chain_id, tx.to_address, is_subject=False)
    for tr in transfers:
        db.insert_transfer(conn, tr)
    conn.commit()

    db.finish_run(conn, run_id, "ok", tx_count, None)
    print(f"  {address}: {len(transactions)} transactions, {len(transfers)} transfers written")
    return "ok", tx_count, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ingest")
    parser.add_argument("--chain", required=True, choices=["ethereum"])
    parser.add_argument("--addresses", required=True, help="path to a file, one address per line")
    parser.add_argument("--blocks", type=int, default=DEFAULT_BLOCKS, help=f"blocks of history to walk back from --to-block or the chain head (default {DEFAULT_BLOCKS})")
    parser.add_argument("--from-block", type=int, default=None, help="fixed start block; overrides --blocks. Use with --to-block for a fully reproducible range")
    parser.add_argument("--to-block", type=int, default=None, help="fixed end block; defaults to the current chain head")
    parser.add_argument("--dry-run", action="store_true", help="fetch and normalise, print counts, write nothing")
    parser.add_argument("--db", default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)

    if args.from_block is not None and args.to_block is not None and args.from_block > args.to_block:
        print("--from-block must be <= --to-block", file=sys.stderr)
        return 1

    addresses = load_addresses(args.addresses)
    if not addresses:
        print("no addresses to ingest", file=sys.stderr)
        return 1

    api_key = load_api_key()
    client = AlchemyClient(api_key=api_key)

    conn = None
    if not args.dry_run:
        conn = db.connect(args.db)
        db.ensure_chain(conn, "ethereum", "ETH", 18)

    if args.to_block is not None:
        to_block = args.to_block
    else:
        to_block = client.latest_block()
    from_block = args.from_block if args.from_block is not None else max(0, to_block - args.blocks)

    print(f"chain=ethereum blocks=[{from_block}, {to_block}] addresses={len(addresses)} dry_run={args.dry_run}")

    any_partial = False
    for address in addresses:
        try:
            status, _tx_count, note = ingest_address(conn, client, address, from_block, to_block, args.dry_run)
        except KeyboardInterrupt:
            print("\ninterrupted; run recorded as partial", file=sys.stderr)
            return 130
        if status == "partial":
            any_partial = True
            print(f"  {address}: PARTIAL — {note}", file=sys.stderr)

    if conn is not None:
        print("row counts:", db.counts(conn))
        conn.close()

    return 1 if any_partial else 0


if __name__ == "__main__":
    raise SystemExit(main())
