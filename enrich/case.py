"""Assembles a Case: a plain, JSON-serialisable dict containing everything
`gate/` needs to reach a verdict, and nothing it would need to fetch itself.

This module does all the database reading so `gate/` never has to. A Case
is also the artefact milestone 4 hands to the model as evidence, so its
shape is evidence from the start: every fact a signal or rule cites must
be traceable back to a field here.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from enrich import signals as signals_mod
from enrich import trace as trace_mod

DEFAULT_HOPS = 2
DEFAULT_DIRECTION = "both"
DEFAULT_MAX_FANOUT = trace_mod.DEFAULT_MAX_FANOUT


def _labels_for(conn: sqlite3.Connection, chain_id: str, address: str) -> list[dict]:
    rows = conn.execute(
        "SELECT label, category, source, retrieved FROM labels WHERE chain_id = ? AND address = ? ORDER BY label",
        (chain_id, address),
    ).fetchall()
    return [{"label": r[0], "category": r[1], "source": r[2], "retrieved": r[3]} for r in rows]


def build_case(
    conn: sqlite3.Connection,
    chain_id: str,
    subject: str,
    hops: int = DEFAULT_HOPS,
    direction: str = DEFAULT_DIRECTION,
    max_fanout: int = DEFAULT_MAX_FANOUT,
    trace_id: int | None = None,
) -> dict:
    """Builds a Case for `subject`. If `trace_id` is given, reuses that
    stored trace (for reproducibility — see `gate replay`); otherwise runs
    a fresh trace now.
    """
    subject = subject.lower()

    if trace_id is None:
        trace_id = trace_mod.run_trace(
            conn, chain_id=chain_id, subject=subject, hops=hops, direction=direction, max_fanout=max_fanout
        )

    run_row = conn.execute(
        "SELECT hops, direction, min_value, status, note FROM trace_runs WHERE id = ?", (trace_id,)
    ).fetchone()
    if run_row is None:
        raise ValueError(f"no trace_runs row for trace_id={trace_id}")
    run_hops, run_direction, run_min_value, run_status, run_note = run_row

    edge_rows = conn.execute(
        """
        SELECT te.hop, te.from_address, te.to_address, te.tx_hash, te.transfer_index,
               te.asset_address, te.amount_raw, te.terminal_reason, t.block_number, t.block_time
        FROM trace_edges te
        LEFT JOIN transactions t ON t.chain_id = ? AND t.tx_hash = te.tx_hash
        WHERE te.trace_id = ?
        ORDER BY te.hop, te.from_address, te.to_address
        """,
        (chain_id, trace_id),
    ).fetchall()

    edges = [
        {
            "hop": hop,
            "from_address": from_a,
            "to_address": to_a,
            "tx_hash": tx_hash,
            "transfer_index": transfer_index,
            "asset_address": asset_address,
            "amount_raw": amount_raw,
            "terminal_reason": terminal_reason,
            "block_number": block_number,
            "block_time": block_time,
        }
        for hop, from_a, to_a, tx_hash, transfer_index, asset_address, amount_raw, terminal_reason, block_number, block_time in edge_rows
    ]

    addresses = {subject}
    for e in edges:
        addresses.add(e["from_address"])
        addresses.add(e["to_address"])

    labels = {addr: _labels_for(conn, chain_id, addr) for addr in sorted(addresses)}

    case = {
        "chain_id": chain_id,
        "subject": subject,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels,
        "trace": {
            "trace_id": trace_id,
            "hops": run_hops,
            "direction": run_direction,
            "min_value": run_min_value,
            "max_fanout": max_fanout,
            "status": run_status,
            "note": run_note,
            "edges": edges,
        },
    }
    # Signals are stored on the Case, not re-derived by gate/ — this is
    # what lets gate/rules.py import nothing from enrich/ at all and stay
    # callable with a hand-written dict.
    case["signals"] = signals_mod.compute_all(case)
    return case
