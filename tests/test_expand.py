from ingest import db as ingest_db
from ingest.models import Transfer

import enrich.__main__ as enrich_cli
from enrich import db


CHAIN = "ethereum"


def add_transfer(conn, tx_hash, from_addr, to_addr, amount_raw):
    tr = Transfer(
        chain_id=CHAIN, tx_hash=tx_hash, transfer_index=0, asset_address=None,
        asset_symbol="ETH", asset_decimals=18, from_address=from_addr, to_address=to_addr,
        amount_raw=amount_raw,
    )
    ingest_db.insert_transfer(conn, tr)
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
