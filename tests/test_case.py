from enrich import db, case as case_mod
from ingest import db as ingest_db
from ingest.models import Transfer

CHAIN = "ethereum"
SUBJECT = "0xsubject000000000000000000000000000000a"
COUNTERPARTY = "0xcounterparty00000000000000000000000000b"


def make_db(tmp_path):
    conn = ingest_db.connect(tmp_path / "test.db")
    db.ensure_schema(conn)
    return conn


def test_build_case_attaches_labels_trace_and_all_five_signals(tmp_path):
    conn = make_db(tmp_path)
    tr = Transfer(
        chain_id=CHAIN, tx_hash="0x" + "1" * 64, transfer_index=0, asset_address=None,
        asset_symbol="ETH", asset_decimals=18, from_address=SUBJECT, to_address=COUNTERPARTY,
        amount_raw="1000000000000000000",
    )
    ingest_db.insert_transfer(conn, tr)
    conn.execute(
        "INSERT INTO labels (chain_id, address, label, category, source, retrieved) VALUES (?, ?, ?, ?, 'test', '2026-09-16')",
        (CHAIN, COUNTERPARTY, "test label", "exchange"),
    )
    conn.commit()

    result = case_mod.build_case(conn, CHAIN, SUBJECT, hops=1, direction="out")

    assert result["subject"] == SUBJECT
    assert result["trace"]["edges"][0]["to_address"] == COUNTERPARTY
    assert result["labels"][COUNTERPARTY][0]["category"] == "exchange"
    assert {s["name"] for s in result["signals"]} == {
        "sanctioned_exposure", "mixer_interaction", "pass_through",
        "counterparty_concentration", "unlabelled_share",
    }


def test_build_case_reuses_an_existing_trace_id_for_replay(tmp_path):
    conn = make_db(tmp_path)
    tr = Transfer(
        chain_id=CHAIN, tx_hash="0x" + "2" * 64, transfer_index=0, asset_address=None,
        asset_symbol="ETH", asset_decimals=18, from_address=SUBJECT, to_address=COUNTERPARTY,
        amount_raw="1000000000000000000",
    )
    ingest_db.insert_transfer(conn, tr)
    conn.commit()

    first = case_mod.build_case(conn, CHAIN, SUBJECT, hops=1, direction="out")
    trace_id = first["trace"]["trace_id"]

    second = case_mod.build_case(conn, CHAIN, SUBJECT, trace_id=trace_id)
    assert second["trace"]["trace_id"] == trace_id
    # same stored trace -> same case, byte-for-byte except the generation timestamp
    first.pop("generated_at")
    second.pop("generated_at")
    assert second == first
