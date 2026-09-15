import ingest.__main__ as cli
from ingest import db
from ingest.evm import AlchemyClient
from tests.fake_rpc import FakeSession


def make_client(monkeypatch, handlers):
    fake_session = FakeSession(handlers)
    client = AlchemyClient(api_key="test-key", session=fake_session, sleep=lambda _s: None)
    monkeypatch.setattr(cli, "AlchemyClient", lambda api_key: client)
    monkeypatch.setattr(cli, "load_api_key", lambda: "test-key")
    return client, fake_session


def base_handlers(address, counterparty, tx_hash):
    def asset_transfers(params):
        query = params[0]
        if query.get("fromAddress") == address:
            return {
                "transfers": [
                    {
                        "uniqueId": "u1",
                        "hash": tx_hash,
                        "from": address,
                        "to": counterparty,
                        "value": 1,
                        "asset": "ETH",
                        "category": "external",
                        "blockNum": hex(50),
                        "rawContract": {"value": hex(10**18), "address": None, "decimal": hex(18)},
                        "metadata": {"blockTimestamp": "2026-09-01T00:00:00.000Z"},
                    }
                ]
            }
        return {"transfers": []}

    def tx_by_hash(params):
        return {
            "hash": tx_hash,
            "blockNumber": hex(50),
            "from": address,
            "to": counterparty,
            "value": hex(10**18),
            "input": "0x",
            "gasPrice": hex(10**10),
        }

    def receipt(params):
        return {"status": "0x1", "gasUsed": hex(21000), "effectiveGasPrice": hex(10**10)}

    return {
        "eth_blockNumber": lambda params: hex(100),
        "alchemy_getAssetTransfers": asset_transfers,
        "eth_getTransactionByHash": tx_by_hash,
        "eth_getTransactionReceipt": receipt,
    }


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "3" * 64
    make_client(monkeypatch, base_handlers(address, counterparty, tx_hash))

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(["--chain", "ethereum", "--addresses", str(addresses_file), "--dry-run", "--db", str(db_path)])

    assert exit_code == 0
    assert not db_path.exists()


def test_running_twice_is_idempotent(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "4" * 64
    make_client(monkeypatch, base_handlers(address, counterparty, tx_hash))

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    cli.main(["--chain", "ethereum", "--addresses", str(addresses_file), "--db", str(db_path)])
    cli.main(["--chain", "ethereum", "--addresses", str(addresses_file), "--db", str(db_path)])

    conn = db.connect(db_path)
    counts = db.counts(conn)
    assert counts["transactions"] == 1
    assert counts["transfers"] == 1
    assert counts["ingest_runs"] == 2  # one row per run, always


def test_rate_limit_exhaustion_records_partial_run(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "5" * 64
    _client, fake_session = make_client(monkeypatch, base_handlers(address, counterparty, tx_hash))
    # Every call to eth_getTransactionByHash gets rate-limited forever.
    fake_session.rate_limit_countdown["eth_getTransactionByHash"] = 999

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(["--chain", "ethereum", "--addresses", str(addresses_file), "--db", str(db_path)])

    assert exit_code == 1
    conn = db.connect(db_path)
    row = conn.execute("SELECT status, note FROM ingest_runs").fetchone()
    assert row[0] == "partial"
    assert row[1] is not None
