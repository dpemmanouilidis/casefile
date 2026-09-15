from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from ingest.models import Transaction, Transfer

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(db_path: str | Path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()
    return conn


def ensure_chain(conn: sqlite3.Connection, chain_id: str, native_symbol: str, native_decimals: int) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO chains (chain_id, native_symbol, native_decimals) VALUES (?, ?, ?)",
        (chain_id, native_symbol, native_decimals),
    )
    conn.commit()


def upsert_address(conn: sqlite3.Connection, chain_id: str, address: str, is_subject: bool) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO addresses (chain_id, address, is_subject, first_seen, last_seen)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(chain_id, address) DO UPDATE SET
            is_subject = MAX(is_subject, excluded.is_subject),
            last_seen = excluded.last_seen
        """,
        (chain_id, address, int(is_subject), now, now),
    )
    conn.commit()


def insert_transaction(conn: sqlite3.Connection, tx: Transaction) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO transactions
            (chain_id, tx_hash, block_number, block_time, from_address, to_address,
             value_raw, fee_raw, status, method_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tx.chain_id,
            tx.tx_hash,
            tx.block_number,
            tx.block_time,
            tx.from_address,
            tx.to_address,
            tx.value_raw,
            tx.fee_raw,
            tx.status,
            tx.method_id,
        ),
    )


def insert_transfer(conn: sqlite3.Connection, tr: Transfer) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO transfers
            (chain_id, tx_hash, transfer_index, asset_address, asset_symbol,
             asset_decimals, from_address, to_address, amount_raw)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            tr.chain_id,
            tr.tx_hash,
            tr.transfer_index,
            tr.asset_address,
            tr.asset_symbol,
            tr.asset_decimals,
            tr.from_address,
            tr.to_address,
            tr.amount_raw,
        ),
    )


def start_run(conn: sqlite3.Connection, chain_id: str, address: str, from_block: int, to_block: int) -> int:
    started_at = datetime.now(timezone.utc).isoformat()
    cursor = conn.execute(
        """
        INSERT INTO ingest_runs (chain_id, address, started_at, from_block, to_block, status)
        VALUES (?, ?, ?, ?, ?, 'running')
        """,
        (chain_id, address, started_at, from_block, to_block),
    )
    conn.commit()
    return cursor.lastrowid


def finish_run(conn: sqlite3.Connection, run_id: int, status: str, tx_count: int, note: str | None = None) -> None:
    finished_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE ingest_runs
        SET finished_at = ?, status = ?, tx_count = ?, note = ?
        WHERE id = ?
        """,
        (finished_at, status, tx_count, note, run_id),
    )
    conn.commit()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    result = {}
    for table in ("chains", "addresses", "transactions", "transfers", "ingest_runs"):
        result[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return result
