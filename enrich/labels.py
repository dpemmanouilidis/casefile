from __future__ import annotations

import csv
import sqlite3
from dataclasses import dataclass
from pathlib import Path

VALID_CATEGORIES = {"exchange", "mixer", "sanctioned", "bridge", "contract", "control", "unknown"}

REQUIRED_COLUMNS = {"chain_id", "address", "label", "category", "source", "retrieved"}
BULK_REQUIRED_COLUMNS = {"chain_id", "address", "entity", "source", "retrieved"}


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


def load_entity_categories(path: str | Path) -> dict[str, str]:
    """Loads the small hand-maintained entity -> category mapping. An
    entity not present here is unmapped, and unmapped always resolves to
    'unknown' when loading the bulk tier — never a guess.
    """
    path = Path(path)
    mapping: dict[str, str] = {}
    reader = csv.DictReader(_strip_comment_lines(path))
    if reader.fieldnames is None or set(reader.fieldnames) != {"entity", "category"}:
        raise MalformedLabelFile(f"{path}: header must be exactly ['category', 'entity'], got {reader.fieldnames}")
    for i, raw in enumerate(reader, start=2):
        category = raw["category"].strip()
        if category not in VALID_CATEGORIES:
            raise MalformedLabelFile(f"{path}:{i}: category {category!r} is not one of {sorted(VALID_CATEGORIES)}")
        mapping[raw["entity"].strip()] = category
    return mapping


def load_bulk_label_file(path: str | Path, entity_categories: dict[str, str]) -> list[LabelRow]:
    """Parses a bulk (third-party scrape) labels file. Unlike the standard
    tier, this file carries no category of its own — every row's `entity`
    is looked up in `entity_categories`, and an entity with no mapping
    resolves to 'unknown' rather than being guessed at or rejected. The
    `source` column already identifies this as a third-party, unverified
    scrape (enforced in the file's own header, not re-validated here).
    """
    path = Path(path)
    reader = csv.DictReader(_strip_comment_lines(path))
    if reader.fieldnames is None or set(reader.fieldnames) != BULK_REQUIRED_COLUMNS:
        raise MalformedLabelFile(
            f"{path}: header must be exactly {sorted(BULK_REQUIRED_COLUMNS)}, got {reader.fieldnames}"
        )

    rows = []
    for i, raw in enumerate(reader, start=2):
        address = raw["address"].strip().lower()
        if not address.startswith("0x"):
            raise MalformedLabelFile(f"{path}:{i}: address {raw['address']!r} does not look like an EVM address")
        entity = raw["entity"].strip()
        category = entity_categories.get(entity, "unknown")
        rows.append(
            LabelRow(
                chain_id=raw["chain_id"].strip(),
                address=address,
                label=entity,
                category=category,
                source=raw["source"].strip(),
                retrieved=raw["retrieved"].strip(),
            )
        )
    return rows


def is_bulk_tier_file(path: str | Path) -> bool:
    reader = csv.DictReader(_strip_comment_lines(Path(path)))
    return reader.fieldnames is not None and set(reader.fieldnames) == BULK_REQUIRED_COLUMNS


def _write_rows(conn: sqlite3.Connection, rows: list[LabelRow]) -> tuple[int, int]:
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


def load_labels_into_db(conn: sqlite3.Connection, path: str | Path) -> tuple[int, int]:
    """Loads one standard-tier labels file. Idempotent and scoped to the
    file: any existing labels rows whose `source` matches a source value
    present in this file are replaced by exactly what the file says now;
    rows from other files (different source values) are left alone. This
    is what lets re-running the same file settle to the same row count
    instead of accumulating duplicates, while still letting a removed row
    disappear.
    """
    return _write_rows(conn, load_label_file(path))


def load_bulk_labels_into_db(
    conn: sqlite3.Connection, path: str | Path, entity_categories: dict[str, str]
) -> tuple[int, int]:
    """Same idempotency contract as load_labels_into_db, for the bulk
    (third-party scrape) tier: category comes from `entity_categories`,
    resolved fresh on every load, so editing entity_categories.csv and
    reloading is how a mapping decision takes effect — no need to
    regenerate the multi-thousand-row bulk file itself.
    """
    return _write_rows(conn, load_bulk_label_file(path, entity_categories))
