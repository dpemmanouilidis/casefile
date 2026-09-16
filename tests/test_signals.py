"""Signals are pure functions over a plain Case dict — every test here is
a hand-written dict, no database, matching the discipline gate/ will need
one layer up.
"""

from enrich import signals

SUBJECT = "0xsubject"


def make_case(edges, labels=None, note=None):
    return {
        "chain_id": "ethereum",
        "subject": SUBJECT,
        "labels": labels or {},
        "trace": {"trace_id": 1, "hops": 2, "direction": "both", "note": note, "edges": edges},
    }


def edge(hop, from_a, to_a, tx_hash, amount_raw="1000000000000000000", asset_address=None, terminal_reason=None, block_number=100, transfer_index=0):
    return {
        "hop": hop, "from_address": from_a, "to_address": to_a, "tx_hash": tx_hash,
        "transfer_index": transfer_index, "asset_address": asset_address, "amount_raw": amount_raw,
        "terminal_reason": terminal_reason, "block_number": block_number, "block_time": "2026-01-01T00:00:00Z",
    }


def label(category, name="test"):
    return [{"label": name, "category": category, "source": "test", "retrieved": "2026-09-16"}]


# --- sanctioned_exposure ---

def test_sanctioned_exposure_flags_direct_sanctioned_counterparty():
    case = make_case(
        edges=[edge(1, SUBJECT, "0xsanctioned", "0xtx1")],
        labels={"0xsanctioned": label("sanctioned")},
    )
    result = signals.sanctioned_exposure(case)
    assert result["complete"] is True
    assert result["value"]["touches"] == [{"hop": 1, "address": "0xsanctioned", "tx_hash": "0xtx1", "direction": "received"}]
    assert "0xtx1" in result["evidence"]
    assert "0xsanctioned" in result["evidence"]


def test_sanctioned_exposure_reaches_hop_two_via_reconstructed_path():
    case = make_case(
        edges=[
            edge(1, SUBJECT, "0xmiddle", "0xtx1"),
            edge(2, "0xmiddle", "0xsanctioned", "0xtx2"),
        ],
        labels={"0xsanctioned": label("sanctioned")},
    )
    result = signals.sanctioned_exposure(case)
    assert result["value"]["touches"] == [{"hop": 2, "address": "0xsanctioned", "tx_hash": "0xtx2", "direction": "received"}]


def test_sanctioned_exposure_ignores_subjects_own_sanctioned_label():
    """The subject's own status is subject_sanctioned's job, not exposure's
    — this must never be conflated (per the milestone-3 correction).
    """
    case = make_case(
        edges=[edge(1, SUBJECT, "0xordinary", "0xtx1")],
        labels={SUBJECT: label("sanctioned"), "0xordinary": []},
    )
    result = signals.sanctioned_exposure(case)
    assert result["value"]["touches"] == []
    assert result["evidence"] == []


def test_sanctioned_exposure_incomplete_when_trace_has_gaps():
    case = make_case(
        edges=[edge(1, SUBJECT, "0xdark", "0xtx1", terminal_reason="not_ingested")],
        labels={},
    )
    result = signals.sanctioned_exposure(case)
    assert result["complete"] is False
    assert "1 hop-1 node(s) not_ingested" in result["gaps"]


# --- mixer_interaction ---

def test_mixer_interaction_counts_both_directions_separately():
    case = make_case(
        edges=[
            edge(1, SUBJECT, "0xmixer", "0xtx1"),
            edge(1, "0xmixer2", SUBJECT, "0xtx2"),
        ],
        labels={"0xmixer": label("mixer"), "0xmixer2": label("mixer")},
    )
    result = signals.mixer_interaction(case)
    assert result["value"] == {"sent_to_mixer_count": 1, "received_from_mixer_count": 1}


def test_mixer_interaction_ignores_non_mixer_counterparties():
    case = make_case(edges=[edge(1, SUBJECT, "0xordinary", "0xtx1")], labels={})
    result = signals.mixer_interaction(case)
    assert result["value"] == {"sent_to_mixer_count": 0, "received_from_mixer_count": 0}


# --- pass_through ---

def test_pass_through_matches_received_then_forwarded_within_window_and_tolerance():
    case = make_case(
        edges=[
            edge(1, "0xsender", SUBJECT, "0xtx_in", amount_raw="1000000000000000000", block_number=100),
            edge(1, SUBJECT, "0xreceiver", "0xtx_out", amount_raw="980000000000000000", block_number=110),  # 2% less, within 5%
        ],
    )
    result = signals.pass_through(case)
    assert result["value"]["match_count"] == 1
    assert result["value"]["matches"][0]["received_tx"] == "0xtx_in"
    assert result["value"]["matches"][0]["forwarded_tx"] == "0xtx_out"


def test_pass_through_rejects_amount_outside_tolerance():
    case = make_case(
        edges=[
            edge(1, "0xsender", SUBJECT, "0xtx_in", amount_raw="1000000000000000000", block_number=100),
            edge(1, SUBJECT, "0xreceiver", "0xtx_out", amount_raw="500000000000000000", block_number=110),  # 50% less
        ],
    )
    result = signals.pass_through(case)
    assert result["value"]["match_count"] == 0


def test_pass_through_rejects_outside_block_window():
    case = make_case(
        edges=[
            edge(1, "0xsender", SUBJECT, "0xtx_in", amount_raw="1000000000000000000", block_number=100),
            edge(1, SUBJECT, "0xreceiver", "0xtx_out", amount_raw="1000000000000000000", block_number=300),  # far outside window
        ],
    )
    result = signals.pass_through(case)
    assert result["value"]["match_count"] == 0


# --- counterparty_concentration ---

def test_counterparty_concentration_finds_largest_share():
    case = make_case(
        edges=[
            edge(1, SUBJECT, "0xbig", "0xtx1", amount_raw="9000000000000000000"),
            edge(1, SUBJECT, "0xsmall", "0xtx2", amount_raw="1000000000000000000"),
        ],
    )
    result = signals.counterparty_concentration(case)
    assert result["value"]["top_counterparty"] == "0xbig"
    assert result["value"]["top_counterparty_share"] == 0.9
    assert result["value"]["distinct_counterparties"] == 2


def test_counterparty_concentration_empty_when_no_hop1_edges():
    case = make_case(edges=[])
    result = signals.counterparty_concentration(case)
    assert result["value"]["top_counterparty"] is None
    assert result["value"]["distinct_counterparties"] == 0


# --- unlabelled_share ---

def test_unlabelled_share_computes_proportion():
    case = make_case(
        edges=[
            edge(1, SUBJECT, "0xknown", "0xtx1"),
            edge(1, SUBJECT, "0xunknown1", "0xtx2"),
            edge(1, SUBJECT, "0xunknown2", "0xtx3"),
        ],
        labels={"0xknown": label("exchange")},
    )
    result = signals.unlabelled_share(case)
    assert result["value"]["unlabelled_count"] == 2
    assert result["value"]["total_counterparties"] == 3
    assert abs(result["value"]["unlabelled_share"] - (2 / 3)) < 1e-9


def test_unlabelled_share_is_zero_with_no_counterparties():
    case = make_case(edges=[])
    result = signals.unlabelled_share(case)
    assert result["value"]["unlabelled_share"] == 0.0


def test_all_signals_importable_with_no_ingest_or_enrich_db_dependency():
    """gate/ will need the same guarantee one layer up; check it holds
    here first since signals is the layer gate reads from.
    """
    import inspect

    source = inspect.getsource(signals)
    assert "sqlite3" not in source
    assert "import ingest" not in source
