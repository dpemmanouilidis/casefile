"""narrate/model.py integration test. This is the one place the suite is
allowed to touch a live model — everything else in tests/ is offline by
design. Skipped, not failed, when Ollama isn't reachable, so `pytest`
still passes on a machine without the local stack running (rule 7 in
docs/milestone-4.md's acceptance criteria).
"""

import urllib.request

import pytest

from narrate import model as model_mod

SUBJECT = "0x" + "1" * 40
REAL_TX = "0x" + "aa" * 32


def _ollama_reachable() -> bool:
    try:
        urllib.request.urlopen(model_mod.OLLAMA_HOST, timeout=2)
        return True
    except Exception:
        return False


def _model_pulled(model: str) -> bool:
    if not _ollama_reachable():
        return False
    try:
        req = urllib.request.Request(f"{model_mod.OLLAMA_HOST}/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            import json

            tags = {m["name"] for m in json.loads(resp.read())["models"]}
        return model in tags
    except Exception:
        return False


TEST_MODEL = "qwen3.5:9b"

pytestmark = pytest.mark.skipif(
    not _model_pulled(TEST_MODEL),
    reason=f"Ollama unreachable or {TEST_MODEL} not pulled — see docs/milestone-4.md acceptance criterion 7",
)


def make_case():
    return {
        "chain_id": "ethereum",
        "subject": SUBJECT,
        "labels": {},
        "trace": {"trace_id": 1, "hops": 1, "direction": "both", "edges": []},
        "signals": [],
    }


def make_verdict():
    return {
        "verdict": "CLEAR",
        "confidence": {"level": "high", "reason": "test"},
        "rules": [
            {"rule": "subject_sanctioned", "outcome": "PASS", "reason": "no label", "evidence": [], "signal": None},
        ],
    }


def test_narrate_verdict_against_live_model_returns_a_result():
    """Not asserting the model gets it right — just that the live
    integration path (real HTTP call, real verify_narrative pass) runs
    end to end and always returns a usable result, model or template.
    """
    result = model_mod.narrate_verdict(
        make_case(), make_verdict(), verdict_id=999999, model=TEST_MODEL, quantization="Q4_K_M",
        max_attempts=1,
    )
    assert result["source"] in ("model", "template")
    assert result["text"]
