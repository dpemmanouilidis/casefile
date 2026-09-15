from ingest import db
from ingest.models import Transaction, Transfer

TX = Transaction(
    chain_id="ethereum",
    tx_hash="0x" + "1" * 64,
    block_number=100,
    block_time="2026-09-01T00:00:00Z",
    from_address="0xaaaa000000000000000000000000000000000a",
    to_address="0xbbbb000000000000000000000000000000000b",
    value_raw="1500000000000000000",
    fee_raw="420000000000000",
    status="success",
    method_id=None,
)

TR = Transfer(
    chain_id="ethereum",
    tx_hash=TX.tx_hash,
    transfer_index=0,
    asset_address=None,
    asset_symbol="ETH",
    asset_decimals=18,
    from_address=TX.from_address,
    to_address=TX.to_address,
    amount_raw="1500000000000000000",
)


def test_schema_creates_all_tables(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    assert db.counts(conn) == {
        "chains": 0,
        "addresses": 0,
        "transactions": 0,
        "transfers": 0,
        "ingest_runs": 0,
    }


def test_insert_transaction_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.insert_transaction(conn, TX)
    db.insert_transaction(conn, TX)
    conn.commit()
    assert db.counts(conn)["transactions"] == 1


def test_insert_transfer_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.insert_transfer(conn, TR)
    db.insert_transfer(conn, TR)
    conn.commit()
    assert db.counts(conn)["transfers"] == 1


def test_upsert_address_keeps_is_subject_true_once_set(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    db.upsert_address(conn, "ethereum", "0xaaaa", is_subject=False)
    db.upsert_address(conn, "ethereum", "0xaaaa", is_subject=True)
    db.upsert_address(conn, "ethereum", "0xaaaa", is_subject=False)
    row = conn.execute("SELECT is_subject FROM addresses WHERE address = ?", ("0xaaaa",)).fetchone()
    assert row[0] == 1


def test_run_lifecycle_ok(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    run_id = db.start_run(conn, "ethereum", "0xaaaa", 0, 100)
    row = conn.execute("SELECT status FROM ingest_runs WHERE id = ?", (run_id,)).fetchone()
    assert row[0] == "running"

    db.finish_run(conn, run_id, "ok", tx_count=5)
    row = conn.execute("SELECT status, tx_count, finished_at FROM ingest_runs WHERE id = ?", (run_id,)).fetchone()
    assert row[0] == "ok"
    assert row[1] == 5
    assert row[2] is not None


def test_run_lifecycle_partial_records_note(tmp_path):
    conn = db.connect(tmp_path / "test.db")
    run_id = db.start_run(conn, "ethereum", "0xaaaa", 0, 100)
    db.finish_run(conn, run_id, "partial", tx_count=0, note="rate-limited after 5 retries")
    row = conn.execute("SELECT status, note FROM ingest_runs WHERE id = ?", (run_id,)).fetchone()
    assert row[0] == "partial"
    assert "rate-limited" in row[1]
