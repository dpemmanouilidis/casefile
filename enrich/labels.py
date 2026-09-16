from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

VALID_CATEGORIES = {"exchange", "mixer", "sanctioned", "bridge", "contract", "control", "unknown"}

REQUIRED_COLUMNS = {"chain_id", "address", "label", "category", "source", "retrieved"}


class MalformedLabelFile(Exception):
    pass


@dataclass(frozen=True)
class LabelRow:
    chain_id: str
    address: str
    label: str
    category: str
    source: str
    retrieved: str


def _strip_comment_lines(path: Path):
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.lstrip().startswith("#"):
                continue
            yield line


def load_label_file(path: str | Path) -> list[LabelRow]:
    """Parse and validate a labels CSV. Raises MalformedLabelFile on the
    first bad row rather than writing anything — a malformed category must
    be rejected, not silently written (rule 3: fail closed).
    """
    path = Path(path)
    reader = csv.DictReader(_strip_comment_lines(path))
    if reader.fieldnames is None or set(reader.fieldnames) != REQUIRED_COLUMNS:
        raise MalformedLabelFile(
            f"{path}: header must be exactly {sorted(REQUIRED_COLUMNS)}, got {reader.fieldnames}"
        )

    rows = []
    for i, raw in enumerate(reader, start=2):  # header is line 1 (post comment-stripping)
        category = raw["category"].strip()
        if category not in VALID_CATEGORIES:
            raise MalformedLabelFile(
                f"{path}:{i}: category {category!r} is not one of {sorted(VALID_CATEGORIES)}"
            )
        address = raw["address"].strip().lower()
        if not address.startswith("0x"):
            raise MalformedLabelFile(f"{path}:{i}: address {raw['address']!r} does not look like an EVM address")
        rows.append(
            LabelRow(
                chain_id=raw["chain_id"].strip(),
                address=address,
                label=raw["label"].strip(),
                category=category,
                source=raw["source"].strip(),
                retrieved=raw["retrieved"].strip(),
            )
        )
    return rows


def load_labels_into_db(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int]:
    """Loads one labels file. Idempotent and scoped to the file: any
    existing labels rows whose `source` matches a source value present in
    this file are replaced by exactly what the file says now; rows from
    other files (different source values) are left alone. This is what
    lets re-running the same file settle to the same row count instead of
    accumulating duplicates, while still letting a removed row disappear.
    """
    rows = load_label_file(path)
    sources_in_file = {r.source for r in rows}

    deleted = 0
    for source in sources_in_file:
        cur = conn.execute("DELETE FROM labels WHERE source = ?", (source,))
        deleted += cur.rowcount

    for r in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO labels (chain_id, address, label, category, source, retrieved)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (r.chain_id, r.address, r.label, r.category, r.source, r.retrieved),
        )
    conn.commit()
    return len(rows), deleted
