from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def ensure_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def save_verdict(conn: sqlite3.Connection, chain_id: str, case: dict, verdict: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO verdicts (chain_id, subject, verdict, case_id, case_json, rules_json, generated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            chain_id,
            verdict["subject"],
            verdict["verdict"],
            verdict["case_id"],
            json.dumps(case),
            json.dumps(verdict["rules"]),
            verdict["generated_at"],
        ),
    )
    conn.commit()
    return cur.lastrowid


def load_verdict(conn: sqlite3.Connection, verdict_id: int) -> dict | None:
    row = conn.execute(
        "SELECT chain_id, subject, verdict, case_id, case_json, rules_json, generated_at FROM verdicts WHERE id = ?",
        (verdict_id,),
    ).fetchone()
    if row is None:
        return None
    chain_id, subject, verdict, case_id, case_json, rules_json, generated_at = row
    return {
        "chain_id": chain_id,
        "subject": subject,
        "verdict": verdict,
        "case_id": case_id,
        "case": json.loads(case_json),
        "rules": json.loads(rules_json),
        "generated_at": generated_at,
    }
