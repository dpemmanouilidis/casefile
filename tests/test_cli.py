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


def test_explicit_block_range_is_used_verbatim_and_skips_latest_block_lookup(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "6" * 64
    _client, fake_session = make_client(monkeypatch, base_handlers(address, counterparty, tx_hash))

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(
        [
            "--chain", "ethereum",
            "--addresses", str(addresses_file),
            "--from-block", "10",
            "--to-block", "60",
            "--db", str(db_path),
        ]
    )

    assert exit_code == 0
    assert ("eth_blockNumber", []) not in fake_session.call_log
    run = db.connect(db_path).execute("SELECT from_block, to_block FROM ingest_runs").fetchone()
    assert run == (10, 60)


def test_from_block_after_to_block_is_rejected(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "7" * 64
    make_client(monkeypatch, base_handlers(address, counterparty, tx_hash))

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")

    exit_code = cli.main(
        [
            "--chain", "ethereum",
            "--addresses", str(addresses_file),
            "--from-block", "100",
            "--to-block", "10",
            "--db", str(tmp_path / "casefile.db"),
        ]
    )
    assert exit_code == 1


def test_transfer_with_unfetchable_parent_tx_is_dropped_and_run_marked_partial(tmp_path, monkeypatch):
    """A transfer whose parent transaction lookup returns None (e.g. reorg or
    indexing lag) must never be written without a matching transactions row —
    every downstream claim cites a transaction hash present in the evidence.
    """
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"
    tx_hash = "0x" + "8" * 64
    handlers = base_handlers(address, counterparty, tx_hash)
    handlers["eth_getTransactionByHash"] = lambda params: None
    make_client(monkeypatch, handlers)

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(["--chain", "ethereum", "--addresses", str(addresses_file), "--db", str(db_path)])

    assert exit_code == 1
    conn = db.connect(db_path)
    counts = db.counts(conn)
    assert counts["transactions"] == 0
    assert counts["transfers"] == 0  # dropped, not orphaned

    orphans = conn.execute(
        """
        SELECT COUNT(*) FROM transfers t
        LEFT JOIN transactions x ON t.chain_id = x.chain_id AND t.tx_hash = x.tx_hash
        WHERE x.tx_hash IS NULL
        """
    ).fetchone()[0]
    assert orphans == 0

    run = conn.execute("SELECT status, note FROM ingest_runs").fetchone()
    assert run[0] == "partial"
    assert tx_hash in run[1]


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


def _probe_transfer(address, counterparty, block):
    return {
        "uniqueId": f"probe-{block}",
        "hash": "0x" + "e" * 64,
        "from": address,
        "to": counterparty,
        "value": 1,
        "asset": "ETH",
        "category": "external",
        "blockNum": hex(block),
        "rawContract": {"value": hex(10**18), "address": None, "decimal": hex(18)},
        "metadata": {"blockTimestamp": "2020-01-01T00:00:00.000Z"},
    }


def test_zero_result_window_with_activity_outside_it_is_marked_partial(tmp_path, monkeypatch):
    """A window that genuinely misses an address's real activity (the bug
    the two-OFAC-subject dormant-window incident exposed) must never look
    identical to a truly quiet address.
    """
    address = "0xaaaa000000000000000000000000000000000a"
    counterparty = "0xbbbb000000000000000000000000000000000b"

    def asset_transfers(params):
        query = params[0]
        max_count = int(query["maxCount"], 16)
        from_block = int(query["fromBlock"], 16)
        if query.get("fromAddress") == address and max_count == 1 and from_block == 0:
            # The full-range [0, to_block] existence probe: this address has
            # one old transfer, well before the requested window.
            return {"transfers": [_probe_transfer(address, counterparty, 5)]}
        # The requested window itself (and the toAddress-role probes) find
        # nothing — that's the bug: the window is just wrong.
        return {"transfers": []}

    handlers = {
        "eth_blockNumber": lambda params: hex(100),
        "alchemy_getAssetTransfers": asset_transfers,
        "eth_getTransactionByHash": lambda params: None,
        "eth_getTransactionReceipt": lambda params: None,
    }
    make_client(monkeypatch, handlers)

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(
        [
            "--chain", "ethereum",
            "--addresses", str(addresses_file),
            "--from-block", "80",
            "--to-block", "100",
            "--db", str(db_path),
        ]
    )

    assert exit_code == 1
    conn = db.connect(db_path)
    run = conn.execute("SELECT status, note FROM ingest_runs").fetchone()
    assert run[0] == "partial"
    assert "has activity between blocks 5 and 5" in run[1]
    assert "window [80, 100]" in run[1]


def test_genuinely_empty_address_is_recorded_as_verified_empty_ok(tmp_path, monkeypatch):
    address = "0xaaaa000000000000000000000000000000000a"

    handlers = {
        "eth_blockNumber": lambda params: hex(100),
        "alchemy_getAssetTransfers": lambda params: {"transfers": []},
        "eth_getTransactionByHash": lambda params: None,
        "eth_getTransactionReceipt": lambda params: None,
    }
    make_client(monkeypatch, handlers)

    addresses_file = tmp_path / "addresses.txt"
    addresses_file.write_text(address + "\n")
    db_path = tmp_path / "casefile.db"

    exit_code = cli.main(
        [
            "--chain", "ethereum",
            "--addresses", str(addresses_file),
            "--from-block", "80",
            "--to-block", "100",
            "--db", str(db_path),
        ]
    )

    assert exit_code == 0
    conn = db.connect(db_path)
    run = conn.execute("SELECT status, note FROM ingest_runs").fetchone()
    assert run[0] == "ok"
    assert run[1] == "verified empty: no activity found for this address in [0, 100]"
