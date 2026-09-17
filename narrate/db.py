from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def ensure_schema(conn: sqlite3.Connection) -> None:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.commit()


def save_narrative(conn: sqlite3.Connection, verdict_id: int, result: dict) -> int:
    """`result` is narrate/model.py's narrate_verdict() return value. A
    narrative that cannot be traced to the exact verdict and model that
    produced it is not evidence — see docs/milestone-4.md — so every field
    needed to reproduce or audit the call is stored, not just the text.
    """
    cur = conn.execute(
        """
        INSERT INTO narratives (
            verdict_id, source, text, model, quantization, think, temperature,
            base_seed, successful_seed, num_ctx, successful_attempt,
            attempts_json, verification_json, generated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            verdict_id,
            result["source"],
            result["text"],
            result["model"],
            result["quantization"],
            int(result["think"]),
            result["temperature"],
            result["base_seed"],
            result["seed"],
            result["num_ctx"],
            result["successful_attempt"],
            json.dumps(result["attempts"]),
            json.dumps(result["verification"]) if result["verification"] is not None else None,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    conn.commit()
    return cur.lastrowid


def load_narrative(conn: sqlite3.Connection, narrative_id: int) -> dict | None:
    row = conn.execute(
        """
        SELECT id, verdict_id, source, text, model, quantization, think, temperature,
               base_seed, successful_seed, num_ctx, successful_attempt,
               attempts_json, verification_json, generated_at
        FROM narratives WHERE id = ?
        """,
        (narrative_id,),
    ).fetchone()
    if row is None:
        return None
    (
        id_, verdict_id, source, text, model, quantization, think, temperature,
        base_seed, successful_seed, num_ctx, successful_attempt,
        attempts_json, verification_json, generated_at,
    ) = row
    return {
        "id": id_,
        "verdict_id": verdict_id,
        "source": source,
        "text": text,
        "model": model,
        "quantization": quantization,
        "think": bool(think),
        "temperature": temperature,
        "base_seed": base_seed,
        "seed": successful_seed,
        "num_ctx": num_ctx,
        "successful_attempt": successful_attempt,
        "attempts": json.loads(attempts_json),
        "verification": json.loads(verification_json) if verification_json is not None else None,
        "generated_at": generated_at,
    }
