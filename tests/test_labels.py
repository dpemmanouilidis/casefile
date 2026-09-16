import sqlite3

import pytest

from enrich import db, labels
from ingest import db as ingest_db


def write_csv(path, rows_text):
    path.write_text(rows_text, encoding="utf-8")


def connect(tmp_path, name="test.db"):
    """A real database with ingest's schema already applied, mirroring
    production: enrich always runs against a db ingest has already created.
    """
    conn = ingest_db.connect(tmp_path / name)
    db.ensure_schema(conn)
    return conn


def test_load_label_file_parses_valid_rows(tmp_path):
    path = tmp_path / "labels.csv"
    write_csv(
        path,
        "# a comment\n"
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xAAAA000000000000000000000000000000000A,Some Label,exchange,etherscan.io,2026-09-16\n",
    )
    rows = labels.load_label_file(path)
    assert len(rows) == 1
    row = rows[0]
    assert row.address == "0xaaaa000000000000000000000000000000000a"
    assert row.category == "exchange"


def test_load_label_file_rejects_malformed_category(tmp_path):
    path = tmp_path / "labels.csv"
    write_csv(
        path,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Some Label,not-a-real-category,etherscan.io,2026-09-16\n",
    )
    with pytest.raises(labels.MalformedLabelFile):
        labels.load_label_file(path)


def test_load_label_file_rejects_wrong_header(tmp_path):
    path = tmp_path / "labels.csv"
    write_csv(path, "chain_id,address,label\nethereum,0xaaaa,x\n")
    with pytest.raises(labels.MalformedLabelFile):
        labels.load_label_file(path)


def test_malformed_row_writes_nothing(tmp_path):
    """Rule 3: fail closed. A bad row anywhere in the file must reject the
    whole file, not write the valid rows that came before it.
    """
    path = tmp_path / "labels.csv"
    write_csv(
        path,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Good,exchange,etherscan.io,2026-09-16\n"
        "ethereum,0xbbbb000000000000000000000000000000000b,Bad,bogus,etherscan.io,2026-09-16\n",
    )
    conn = connect(tmp_path)
    with pytest.raises(labels.MalformedLabelFile):
        labels.load_labels_into_db(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0


def test_load_labels_into_db_is_idempotent(tmp_path):
    path = tmp_path / "labels.csv"
    write_csv(
        path,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Some Label,exchange,etherscan.io,2026-09-16\n",
    )
    conn = connect(tmp_path)

    labels.load_labels_into_db(conn, path)
    labels.load_labels_into_db(conn, path)

    assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 1


def test_re_running_with_a_row_removed_drops_the_stale_label(tmp_path):
    path = tmp_path / "labels.csv"
    write_csv(
        path,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Label A,exchange,my-source,2026-09-16\n"
        "ethereum,0xbbbb000000000000000000000000000000000b,Label B,exchange,my-source,2026-09-16\n",
    )
    conn = connect(tmp_path)
    labels.load_labels_into_db(conn, path)
    assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 2

    write_csv(
        path,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Label A,exchange,my-source,2026-09-16\n",
    )
    labels.load_labels_into_db(conn, path)
    rows = conn.execute("SELECT address FROM labels").fetchall()
    assert rows == [("0xaaaa000000000000000000000000000000000a",)]


def test_loading_one_file_leaves_another_files_rows_alone(tmp_path):
    file_a = tmp_path / "a.csv"
    file_b = tmp_path / "b.csv"
    write_csv(
        file_a,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xaaaa000000000000000000000000000000000a,Label A,exchange,source-a,2026-09-16\n",
    )
    write_csv(
        file_b,
        "chain_id,address,label,category,source,retrieved\n"
        "ethereum,0xbbbb000000000000000000000000000000000b,Label B,mixer,source-b,2026-09-16\n",
    )
    conn = connect(tmp_path)
    labels.load_labels_into_db(conn, file_a)
    labels.load_labels_into_db(conn, file_b)
    assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 2

    labels.load_labels_into_db(conn, file_a)
    assert conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 2


def test_ensure_schema_adds_is_contract_column_once(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE addresses (
            chain_id TEXT NOT NULL,
            address TEXT NOT NULL,
            is_subject INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT,
            last_seen TEXT,
            PRIMARY KEY (chain_id, address)
        );
        """
    )
    db.ensure_schema(conn)
    db.ensure_schema(conn)  # must not raise "duplicate column" on a second call
    columns = {row[1] for row in conn.execute("PRAGMA table_info(addresses)")}
    assert "is_contract" in columns


def test_label_coverage_counts_labelled_and_unknown(tmp_path):
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE addresses (
            chain_id TEXT NOT NULL, address TEXT NOT NULL, is_subject INTEGER NOT NULL DEFAULT 0,
            first_seen TEXT, last_seen TEXT, PRIMARY KEY (chain_id, address)
        );
        """
    )
    db.ensure_schema(conn)
    conn.execute("INSERT INTO addresses (chain_id, address, is_subject) VALUES ('ethereum', '0xaaaa', 1)")
    conn.execute("INSERT INTO addresses (chain_id, address, is_subject) VALUES ('ethereum', '0xbbbb', 0)")
    conn.execute(
        "INSERT INTO labels (chain_id, address, label, category, source, retrieved) "
        "VALUES ('ethereum', '0xaaaa', 'x', 'exchange', 's', '2026-09-16')"
    )
    conn.commit()

    coverage = db.label_coverage(conn)
    assert coverage == {"total_addresses": 2, "labelled": 1, "unknown": 1}
