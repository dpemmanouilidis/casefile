"""narrate/db.py round-trip tests. No model call — a hand-written result
dict shaped like narrate/model.py's narrate_verdict() return value.
"""

import sqlite3

from narrate import db as narrate_db

MODEL_RESULT = {
    "source": "model",
    "text": "VERDICT: FLAGGED (confidence: low)\nSome narrative text.",
    "model": "qwen3.5:9b",
    "quantization": "Q4_K_M",
    "think": False,
    "temperature": 0.0,
    "base_seed": 42,
    "seed": 9043,
    "num_ctx": 8192,
    "successful_attempt": 1,
    "attempts": [{"attempt": 1, "seed": 9043, "generation": {"text": "..."}, "verification": {"passed": True}}],
    "verification": {"passed": True, "checks": {}, "flags": {}},
}

TEMPLATE_RESULT = {
    **MODEL_RESULT,
    "source": "template",
    "seed": None,
    "successful_attempt": None,
    "verification": None,
}


def make_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "test.db")
    narrate_db.ensure_schema(conn)
    return conn


def test_save_and_load_model_narrative_round_trips(tmp_path):
    conn = make_conn(tmp_path)
    narrative_id = narrate_db.save_narrative(conn, verdict_id=9, result=MODEL_RESULT)

    loaded = narrate_db.load_narrative(conn, narrative_id)

    assert loaded["verdict_id"] == 9
    assert loaded["source"] == "model"
    assert loaded["text"] == MODEL_RESULT["text"]
    assert loaded["seed"] == 9043
    assert loaded["successful_attempt"] == 1
    assert loaded["verification"] == MODEL_RESULT["verification"]
    assert loaded["attempts"] == MODEL_RESULT["attempts"]


def test_save_and_load_template_narrative_has_no_seed_or_verification(tmp_path):
    conn = make_conn(tmp_path)
    narrative_id = narrate_db.save_narrative(conn, verdict_id=9, result=TEMPLATE_RESULT)

    loaded = narrate_db.load_narrative(conn, narrative_id)

    assert loaded["source"] == "template"
    assert loaded["seed"] is None
    assert loaded["successful_attempt"] is None
    assert loaded["verification"] is None


def test_load_narrative_returns_none_for_missing_id(tmp_path):
    conn = make_conn(tmp_path)
    assert narrate_db.load_narrative(conn, 999) is None


def test_ensure_schema_is_idempotent(tmp_path):
    conn = make_conn(tmp_path)
    narrate_db.ensure_schema(conn)
    narrative_id = narrate_db.save_narrative(conn, verdict_id=9, result=MODEL_RESULT)
    assert narrate_db.load_narrative(conn, narrative_id) is not None
