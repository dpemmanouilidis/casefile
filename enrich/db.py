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
