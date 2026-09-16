from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def ensure_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    columns = {row[1] for row in conn.execute("PRAGMA table_info(addresses)")}
    if "is_contract" not in columns:
        # SQLite has no "ADD COLUMN IF NOT EXISTS"; guard with a PRAGMA
        # check instead so this stays idempotent across repeated runs.
        conn.execute("ALTER TABLE addresses ADD COLUMN is_contract INTEGER")
    conn.commit()


def addresses_missing_is_contract(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT address FROM addresses WHERE chain_id = 'ethereum' AND is_contract IS NULL")
    return [r[0] for r in rows]


def set_is_contract(conn: sqlite3.Connection, address: str, is_contract: bool) -> None:
    conn.execute(
        "UPDATE addresses SET is_contract = ? WHERE chain_id = 'ethereum' AND address = ?",
        (int(is_contract), address),
    )


def get_trace_run(conn: sqlite3.Connection, trace_id: int) -> dict | None:
    row = conn.execute(
        "SELECT chain_id, subject, hops, direction, min_value, started_at, finished_at, status, note "
        "FROM trace_runs WHERE id = ?",
        (trace_id,),
    ).fetchone()
    if row is None:
        return None
    keys = ["chain_id", "subject", "hops", "direction", "min_value", "started_at", "finished_at", "status", "note"]
    return dict(zip(keys, row))


def get_trace_edges(conn: sqlite3.Connection, trace_id: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT hop, from_address, to_address, tx_hash, transfer_index, asset_address, amount_raw, terminal_reason
        FROM trace_edges WHERE trace_id = ? ORDER BY hop, from_address, to_address
        """,
        (trace_id,),
    ).fetchall()
    keys = ["hop", "from_address", "to_address", "tx_hash", "transfer_index", "asset_address", "amount_raw", "terminal_reason"]
    return [dict(zip(keys, r)) for r in rows]


def label_for(conn: sqlite3.Connection, chain_id: str, address: str) -> str:
    row = conn.execute(
        "SELECT label FROM labels WHERE chain_id = ? AND address = ? ORDER BY label LIMIT 1",
        (chain_id, address),
    ).fetchone()
    return row[0] if row else "unknown"


def label_coverage(conn: sqlite3.Connection) -> dict:
    total = conn.execute("SELECT COUNT(*) FROM addresses WHERE chain_id = 'ethereum'").fetchone()[0]
    labelled = conn.execute(
        """
        SELECT COUNT(DISTINCT a.address) FROM addresses a
        JOIN labels l ON l.chain_id = a.chain_id AND l.address = a.address
        WHERE a.chain_id = 'ethereum'
        """
    ).fetchone()[0]
    return {"total_addresses": total, "labelled": labelled, "unknown": total - labelled}
