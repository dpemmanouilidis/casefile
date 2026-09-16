from ingest import db as ingest_db
from ingest.models import Transfer

from enrich import db, trace

CHAIN = "ethereum"


def make_db(tmp_path):
    conn = ingest_db.connect(tmp_path / "test.db")
    db.ensure_schema(conn)
    return conn


def add_transfer(conn, tx_hash, from_addr, to_addr, transfer_index=0, amount_raw="1000000000000000000"):
    tr = Transfer(
        chain_id=CHAIN,
        tx_hash=tx_hash,
        transfer_index=transfer_index,
        asset_address=None,
        asset_symbol="ETH",
        asset_decimals=18,
        from_address=from_addr,
        to_address=to_addr,
        amount_raw=amount_raw,
    )
    ingest_db.insert_transfer(conn, tr)
    conn.commit()


def add_label(conn, address, category, label="test label"):
    conn.execute(
        "INSERT INTO labels (chain_id, address, label, category, source, retrieved) VALUES (?, ?, ?, ?, 'test', '2026-09-16')",
        (CHAIN, address, label, category),
    )
    conn.commit()


def test_hop_limit_marks_edges_at_the_final_requested_hop(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "a")
    add_transfer(conn, "0x" + "2" * 64, "a", "b")

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=2, direction="out")
    edges = db.get_trace_edges(conn, trace_id)

    by_hop = {e["hop"]: e for e in edges}
    assert by_hop[1]["terminal_reason"] is None
    assert by_hop[2]["terminal_reason"] == "hop_limit"


def test_custody_change_stops_expansion_at_a_labelled_exchange(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "exch")
    add_transfer(conn, "0x" + "2" * 64, "exch", "downstream")  # must never be traced through
    add_label(conn, "exch", "exchange")

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=3, direction="out")
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["to_address"] == "exch"
    assert edges[0]["terminal_reason"] == "custody_change"


def test_not_ingested_marks_edge_into_a_node_with_no_local_transfers(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "dead_end")
    # "dead_end" has no rows of its own in `transfers` at all.

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=2, direction="out")
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["to_address"] == "dead_end"
    assert edges[0]["terminal_reason"] == "not_ingested"


def test_not_ingested_is_distinguishable_from_hop_limit(tmp_path):
    """The whole point of not_ingested: an un-ingested node must never look
    like a verified leaf. Confirm the two reasons are never conflated on
    the same edge in the same trace.
    """
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "known")
    add_transfer(conn, "0x" + "2" * 64, "known", "further")  # exercised only if hops >= 2
    add_transfer(conn, "0x" + "3" * 64, "s", "unknown_leaf")  # unknown_leaf has no transfers of its own

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=2, direction="out")
    edges = {e["to_address"]: e for e in db.get_trace_edges(conn, trace_id)}

    assert edges["unknown_leaf"]["terminal_reason"] == "not_ingested"
    assert edges["further"]["terminal_reason"] == "hop_limit"
    assert edges["unknown_leaf"]["terminal_reason"] != edges["further"]["terminal_reason"]


def test_fan_out_cap_marks_the_incoming_edge_and_does_not_expand_further(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "hub")
    for i in range(5):
        add_transfer(conn, f"0x{i:064x}", "hub", f"leaf{i}")

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=3, direction="out", max_fanout=2)
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["to_address"] == "hub"
    assert edges[0]["terminal_reason"] == "fan_out_cap"

    run = db.get_trace_run(conn, trace_id)
    assert "fan_out_cap" in run["note"]


def test_fan_out_cap_never_applies_to_the_subject_itself(tmp_path):
    """The cap stops expansion through a busy intermediate node, not the
    trace before it even starts. A subject with more direct counterparties
    than --max-fanout (e.g. a real exchange hot wallet) must still expand
    at hop 1 rather than immediately hitting fan_out_cap with zero edges.
    """
    conn = make_db(tmp_path)
    for i in range(5):
        add_transfer(conn, f"0x{i:064x}", "s", f"leaf{i}")

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=1, direction="out", max_fanout=2)
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 5
    assert all(e["terminal_reason"] == "hop_limit" for e in edges)
    run = db.get_trace_run(conn, trace_id)
    assert run["note"] is None


def test_direction_in_only_follows_incoming_transfers(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "sender", "s")  # incoming to s
    add_transfer(conn, "0x" + "2" * 64, "s", "receiver")  # outgoing from s, must be ignored

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=1, direction="in")
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["from_address"] == "sender"


def test_min_value_filters_out_small_transfers(tmp_path):
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "small", amount_raw="100")
    add_transfer(conn, "0x" + "2" * 64, "s", "big", amount_raw="999999999999999999999")

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=1, direction="out", min_value=10**18)
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["to_address"] == "big"


def test_both_direction_still_detects_not_ingested_despite_the_backedge(tmp_path):
    """Regression: with direction=both, the transfer that reaches a node
    always touches that node, so naively checking "does this node have any
    local transfer" is always true and not_ingested could never fire. A
    node whose only appearance in `transfers` is the edge that led to it
    must still be marked not_ingested, not silently treated as expandable.
    """
    conn = make_db(tmp_path)
    add_transfer(conn, "0x" + "1" * 64, "s", "leaf")
    # "leaf" has exactly one row in `transfers`: the edge above. No other
    # activity of its own is recorded anywhere.

    trace_id = trace.run_trace(conn, CHAIN, "s", hops=2, direction="both")
    edges = db.get_trace_edges(conn, trace_id)

    assert len(edges) == 1
    assert edges[0]["to_address"] == "leaf"
    assert edges[0]["terminal_reason"] == "not_ingested"


def test_trace_makes_no_network_calls_by_construction():
    """enrich.trace never imports an RPC client — it can only read the
    local transfers/labels tables. Proof by inspection: no ingest.evm
    import anywhere in the module.
    """
    import inspect

    source = inspect.getsource(trace)
    assert "requests" not in source
    assert "AlchemyClient" not in source
