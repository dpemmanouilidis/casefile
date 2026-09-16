"""Offline breadth-first tracing over the local `transfers` table.

Makes zero RPC calls. Anything not already ingested is not in the graph,
and expansion says so explicitly (`not_ingested`) rather than looking like
a verified dead end — see the termination-rule docstrings below and
docs/milestone-2.md.
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone

CUSTODY_CATEGORIES = {"exchange", "mixer"}
DEFAULT_MAX_FANOUT = 50
VALID_DIRECTIONS = {"out", "in", "both"}


def _node_categories(conn: sqlite3.Connection, chain_id: str, address: str) -> set[str]:
    rows = conn.execute("SELECT category FROM labels WHERE chain_id = ? AND address = ?", (chain_id, address))
    return {r[0] for r in rows}


def _node_transfers(conn: sqlite3.Connection, chain_id: str, address: str, direction: str, min_value: int | None):
    if direction == "out":
        cond, params = "from_address = ?", (chain_id, address)
    elif direction == "in":
        cond, params = "to_address = ?", (chain_id, address)
    else:
        cond, params = "(from_address = ? OR to_address = ?)", (chain_id, address, address)

    rows = conn.execute(
        f"""
        SELECT tx_hash, transfer_index, from_address, to_address, asset_address, amount_raw
        FROM transfers WHERE chain_id = ? AND {cond}
        """,
        params,
    ).fetchall()

    if min_value is not None:
        rows = [r for r in rows if int(r[5]) >= min_value]
    return rows


def run_trace(
    conn: sqlite3.Connection,
    chain_id: str,
    subject: str,
    hops: int,
    direction: str = "out",
    min_value: int | None = None,
    max_fanout: int = DEFAULT_MAX_FANOUT,
) -> int:
    if hops < 1:
        raise ValueError("hops must be >= 1")
    if direction not in VALID_DIRECTIONS:
        raise ValueError(f"direction must be one of {sorted(VALID_DIRECTIONS)}")

    subject = subject.lower()
    started_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO trace_runs (chain_id, subject, hops, direction, min_value, started_at, status)
        VALUES (?, ?, ?, ?, ?, ?, 'running')
        """,
        (chain_id, subject, hops, direction, str(min_value) if min_value is not None else None, started_at),
    )
    trace_id = cur.lastrowid
    conn.commit()

    notes: list[str] = []

    try:
        # frontier: list of (node_address, [(hop, tx_hash, transfer_index), ...])
        # the second element is every not-yet-terminal edge that led into
        # this node, so that if expansion stops here we can mark all of
        # them rather than just one.
        frontier: list[tuple[str, list[tuple[int, str, int]]]] = [(subject, [])]

        for hop in range(1, hops + 1):
            next_frontier: list[tuple[str, list[tuple[int, str, int]]]] = []

            for node, incoming_keys in frontier:
                is_subject = not incoming_keys and node == subject and hop == 1

                if not is_subject:
                    categories = _node_categories(conn, chain_id, node)
                    if categories & CUSTODY_CATEGORIES:
                        _mark_edges(conn, trace_id, incoming_keys, "custody_change")
                        continue

                rows = _node_transfers(conn, chain_id, node, direction, min_value)
                if incoming_keys:
                    # Exclude the edge(s) that led here from "does this node
                    # have local data" — with direction=both, the transfer
                    # that brought us to this node always touches it, so
                    # counting it would make not_ingested unreachable and
                    # let an un-ingested node masquerade as a verified leaf.
                    incoming_key_set = {(tx_hash, transfer_index) for _, tx_hash, transfer_index in incoming_keys}
                    rows = [r for r in rows if (r[0], r[1]) not in incoming_key_set]
                if not rows:
                    if incoming_keys:
                        _mark_edges(conn, trace_id, incoming_keys, "not_ingested")
                    else:
                        notes.append(f"subject {node} has no local transfers for direction={direction}")
                    continue

                counterparties = {(to_a if from_a == node else from_a) for _, _, from_a, to_a, _, _ in rows}
                if len(counterparties) > max_fanout:
                    if incoming_keys:
                        _mark_edges(conn, trace_id, incoming_keys, "fan_out_cap")
                    notes.append(
                        f"fan_out_cap at {node}: {len(counterparties)} distinct counterparties "
                        f"> --max-fanout {max_fanout}"
                    )
                    continue

                is_final_hop = hop == hops
                new_edges_by_counterparty: dict[str, list[tuple[int, str, int]]] = defaultdict(list)
                for tx_hash, transfer_index, from_a, to_a, asset_addr, amount_raw in rows:
                    counterparty = to_a if from_a == node else from_a
                    terminal_reason = "hop_limit" if is_final_hop else None
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO trace_edges
                            (trace_id, hop, from_address, to_address, tx_hash, transfer_index,
                             asset_address, amount_raw, terminal_reason)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (trace_id, hop, from_a, to_a, tx_hash, transfer_index, asset_addr, amount_raw, terminal_reason),
                    )
                    if not is_final_hop:
                        new_edges_by_counterparty[counterparty].append((hop, tx_hash, transfer_index))

                for counterparty, keys in new_edges_by_counterparty.items():
                    next_frontier.append((counterparty, keys))

            frontier = next_frontier

        conn.commit()
        finished_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE trace_runs SET finished_at = ?, status = 'ok', note = ? WHERE id = ?",
            (finished_at, "; ".join(notes) if notes else None, trace_id),
        )
        conn.commit()
        return trace_id
    except Exception as exc:
        finished_at = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE trace_runs SET finished_at = ?, status = 'failed', note = ? WHERE id = ?",
            (finished_at, str(exc), trace_id),
        )
        conn.commit()
        raise


def _mark_edges(conn: sqlite3.Connection, trace_id: int, keys: list[tuple[int, str, int]], reason: str) -> None:
    for hop, tx_hash, transfer_index in keys:
        conn.execute(
            """
            UPDATE trace_edges SET terminal_reason = ?
            WHERE trace_id = ? AND hop = ? AND tx_hash = ? AND transfer_index = ?
            """,
            (reason, trace_id, hop, tx_hash, transfer_index),
        )
