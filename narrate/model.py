"""Local inference adapter. Talks to the existing Ollama stack on the RTX
5070 over its HTTP API (stdlib urllib only — no new dependency for a single
POST request). No cloud, no API key, no fallback to a hosted model: if
Ollama is unreachable or the requested model isn't pulled, that is an
error (`ModelUnavailable`), never a quiet substitution for a different
model or provider.

Determinism: temperature=0 and a fixed seed are passed through Ollama's
`options` on every call, and both are recorded alongside the output so a
narrative is reproducible from its recorded seed *within one loaded model
session* — two back-to-back calls with no intervening unload/reload are
byte-identical. Across separate loads of the same model, seed and
temperature the same, output was observed to differ (see README's
"Local model findings" — plausibly GPU kernel non-determinism, e.g.
cuBLAS/cuDNN algorithm selection varying per load, not a seeding bug).
The honest claim is "reproducible given the recorded seed and the same
loaded session," not "byte-identical forever." `think` defaults to False
— see narrate/prompt.py and eval/ for why: with thinking enabled, this
stack burns its entire token budget on the reasoning trace before
producing any narrative at all (measured: 6000 tokens / 76s, zero
response characters, on the evidence bundle in eval/). The problem there
is purely that it doesn't finish, not that it wanders — its trace also
reproduced byte-identically within a session in that same test.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from narrate import prompt as prompt_mod
from narrate import verify as verify_mod

OLLAMA_HOST = "http://localhost:11434"
GENERATE_ENDPOINT = f"{OLLAMA_HOST}/api/generate"

DEFAULT_TEMPERATURE = 0.0
DEFAULT_SEED = 42  # arbitrary but fixed — recorded per narrative so any
# run is reproducible from its stored inputs, not because 42 is special.

MAX_NARRATION_ATTEMPTS = 3  # each attempt is a full model call; three gives
# a real chance to recover from a one-off citation slip without burning
# minutes of GPU time chasing a model that fundamentally can't do the task
# on this evidence bundle — sustained failure past 3 should fall back, not
# retry forever.

DEFAULT_NUM_CTX = 8192  # covers the largest bundle measured in eval/
# (3491 prompt tokens for the largest stored verdict) plus response
# headroom, while still fitting qwen3.5:9b-q8_0 on this card's 12GB (see
# eval/ for the VRAM measurement — it fails above ~8192-12288 ctx).


def derive_seed(base_seed: int, verdict_id: int, attempt_num: int) -> int:
    """A different seed per retry attempt, but deterministic from
    (base_seed, verdict_id, attempt_num) — a run is reproducible from its
    stored inputs, it's just that "the seed" is now per-attempt rather than
    one fixed value. Three identical retries under one fixed seed is wasted
    GPU time (the output cannot differ), so retries must vary the seed to
    do anything useful.
    """
    return base_seed + verdict_id * 1000 + attempt_num


class ModelUnavailable(Exception):
    """Ollama is unreachable, or the requested model tag isn't pulled.
    Never caught to silently substitute a different model or a hosted one.
    """


def generate(
    prompt_text: str,
    model: str,
    *,
    think: bool = False,
    temperature: float = DEFAULT_TEMPERATURE,
    seed: int = DEFAULT_SEED,
    num_ctx: int = DEFAULT_NUM_CTX,
    num_predict: int = 800,
) -> dict:
    payload = json.dumps({
        "model": model,
        "prompt": prompt_text,
        "stream": False,
        "think": think,
        "options": {
            "temperature": temperature,
            "seed": seed,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }).encode("utf-8")
    req = urllib.request.Request(GENERATE_ENDPOINT, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError) as exc:
        raise ModelUnavailable(f"model '{model}' unavailable via Ollama at {OLLAMA_HOST}: {exc}") from exc

    return {
        "text": data.get("response", ""),
        "thinking": data.get("thinking", ""),
        "model": model,
        "quantization": None,  # filled in by narrate_verdict, which knows the tag's quant
        "think": think,
        "temperature": temperature,
        "seed": seed,
        "num_ctx": num_ctx,
        "prompt_tokens": data.get("prompt_eval_count"),
        "eval_tokens": data.get("eval_count"),
        "done_reason": data.get("done_reason"),
    }


def build_template_narrative(case: dict, verdict: dict) -> str:
    """Deterministic, model-free fallback built straight from the Verdict.
    Marked unambiguously as a template so it is never mistaken for a
    model-generated, verified narrative — see module docstring and
    narrate/verify.py.
    """
    confidence = verdict["confidence"]
    lines = [
        "[TEMPLATE NARRATIVE — automated narration failed verification; no model output shown]",
        f"Subject: {case['subject']} ({case['chain_id']})",
        f"Verdict: {verdict['verdict']}",
        f"Confidence: {confidence.get('level', 'unknown')} — {confidence.get('reason', '')}",
        "Findings:",
    ]
    for r in verdict["rules"]:
        if r["outcome"] not in ("FLAG", "UNKNOWN"):
            continue
        lines.append(f"  [{r['outcome']}] {r['rule']}: {r['reason']}")
        if r["evidence"]:
            lines.append(f"    evidence: {', '.join(r['evidence'])}")
    return "\n".join(lines)


def narrate_verdict(
    case: dict,
    verdict: dict,
    verdict_id: int,
    model: str,
    quantization: str,
    *,
    think: bool = False,
    max_attempts: int = MAX_NARRATION_ATTEMPTS,
    temperature: float = DEFAULT_TEMPERATURE,
    base_seed: int = DEFAULT_SEED,
    num_ctx: int = DEFAULT_NUM_CTX,
) -> dict:
    """Generates and verifies a narrative, retrying up to `max_attempts`
    times on verification failure, one differently-seeded attempt per try
    (see derive_seed). Falls back to a marked template if no attempt
    passes. Always returns — the system always produces a case file, and
    it is never ambiguous whether it is model-written-and-verified or
    template-generated-and-labelled.

    `verdict_id` identifies the stored verdicts row (see gate/db.py) and is
    required, not optional, because it's half of what makes the per-attempt
    seeds reproducible — a narrative can't be regenerated deterministically
    without knowing which verdict it was for.
    """
    prompt_text = prompt_mod.build_prompt(case, verdict)
    attempts = []

    for attempt_num in range(1, max_attempts + 1):
        seed = derive_seed(base_seed, verdict_id, attempt_num)
        result = generate(
            prompt_text, model, think=think, temperature=temperature, seed=seed, num_ctx=num_ctx,
        )
        result["quantization"] = quantization
        verification = verify_mod.verify_narrative(result["text"], case, verdict)
        attempts.append({"attempt": attempt_num, "seed": seed, "generation": result, "verification": verification})
        if verification["passed"]:
            return {
                "source": "model",
                "text": result["text"],
                "model": model,
                "quantization": quantization,
                "think": think,
                "temperature": temperature,
                "base_seed": base_seed,
                "seed": seed,
                "num_ctx": num_ctx,
                "attempts": attempts,
                "successful_attempt": attempt_num,
                "verification": verification,
            }

    template_text = build_template_narrative(case, verdict)
    return {
        "source": "template",
        "text": template_text,
        "model": model,
        "quantization": quantization,
        "think": think,
        "temperature": temperature,
        "base_seed": base_seed,
        "seed": None,
        "num_ctx": num_ctx,
        "attempts": attempts,
        "successful_attempt": None,
        "verification": None,
    }
