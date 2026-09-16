from ingest import db as ingest_db
from ingest.models import Transaction, Transfer

import enrich.__main__ as enrich_cli
from enrich import db


CHAIN = "ethereum"


def add_transfer(conn, tx_hash, from_addr, to_addr, amount_raw, block_number=50):
    tr = Transfer(
        chain_id=CHAIN, tx_hash=tx_hash, transfer_index=0, asset_address=None,
        asset_symbol="ETH", asset_decimals=18, from_address=from_addr, to_address=to_addr,
        amount_raw=amount_raw,
    )
    ingest_db.insert_transfer(conn, tr)
    tx = Transaction(
        chain_id=CHAIN, tx_hash=tx_hash, block_number=block_number, block_time="2020-01-01T00:00:00Z",
        from_address=from_addr, to_address=to_addr, value_raw=amount_raw, fee_raw=None,
        status="success", method_id=None,
    )
    ingest_db.insert_transaction(conn, tx)
    conn.commit()


def add_label(conn, address, category):
    conn.execute(
        "INSERT INTO labels (chain_id, address, label, category, source, retrieved) VALUES (?, ?, ?, ?, 'test', '2026-09-16')",
        (CHAIN, address, "test", category),
    )
    conn.commit()


def test_expand_ranks_by_native_eth_value_and_excludes_custody_nodes(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = ingest_db.connect(db_path)
    db.ensure_schema(conn)

    add_transfer(conn, "0x" + "1" * 64, "s", "small", "1000000000000000000")     # 1 ETH
    add_transfer(conn, "0x" + "2" * 64, "s", "big", "9000000000000000000")       # 9 ETH
    add_transfer(conn, "0x" + "3" * 64, "s", "exch", "5000000000000000000")      # 5 ETH, but exchange
    add_label(conn, "exch", "exchange")
    conn.close()

    ingested = []

    def fake_ingest_address(conn, client, address, from_block, to_block, dry_run):
        ingested.append(address)
        return "ok", 0, None

    monkeypatch.setattr(enrich_cli, "ingest_address", fake_ingest_address)
    monkeypatch.setattr(enrich_cli, "load_api_key", lambda: "test-key")

    class FakeClient:
        def latest_block(self):
            return 100

    monkeypatch.setattr(enrich_cli, "AlchemyClient", lambda api_key: FakeClient())

    exit_code = enrich_cli.main(
        ["expand", "--chain", "ethereum", "--address", "s", "--top", "5", "--db", str(db_path)]
    )

    assert exit_code == 0
    assert ingested == ["big", "small"]  # ranked by value, exch excluded entirely


def test_expand_anchors_window_to_counterpartys_own_known_block_not_chain_head(tmp_path, monkeypatch):
    """Regression: ranking is over a counterparty's full known history,
    which can be years old. A window relative to the current chain head
    (like ingest's default) would almost always miss it entirely — this
    is exactly what happened the first time this command was run against
    long-dormant OFAC subjects (162 RPC calls, 0 rows written). The window
    must be centered on the counterparty's own last known interaction
    block instead.
    """
    db_path = tmp_path / "test.db"
    conn = ingest_db.connect(db_path)
    db.ensure_schema(conn)

    old_block = 9_000_000  # years before the current chain head
    add_transfer(conn, "0x" + "1" * 64, "s", "old_counterparty", "1000000000000000000", block_number=old_block)
    conn.close()

    seen_windows = []

    def fake_ingest_address(conn, client, address, from_block, to_block, dry_run):
        seen_windows.append((address, from_block, to_block))
        return "ok", 0, None

    monkeypatch.setattr(enrich_cli, "ingest_address", fake_ingest_address)
    monkeypatch.setattr(enrich_cli, "load_api_key", lambda: "test-key")

    current_head = 25_000_000

    class FakeClient:
        def latest_block(self):
            return current_head

    monkeypatch.setattr(enrich_cli, "AlchemyClient", lambda api_key: FakeClient())

    exit_code = enrich_cli.main(
        ["expand", "--chain", "ethereum", "--address", "s", "--top", "5", "--blocks", "5000", "--db", str(db_path)]
    )

    assert exit_code == 0
    assert len(seen_windows) == 1
    address, from_block, to_block = seen_windows[0]
    assert address == "old_counterparty"
    # window must straddle the counterparty's own known block, not chain head
    assert from_block <= old_block <= to_block
    assert to_block < current_head - 1000  # nowhere near the head-relative window
