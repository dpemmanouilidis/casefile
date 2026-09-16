# Milestone 4 — narrate: the model, behind the gate

**Goal:** a local model turns a Case and its Verdict into an analyst-readable narrative
in which every factual sentence cites evidence that exists, the verdict is never
changed, and a failure to verify produces an explicit, marked fallback rather than
prose nobody checked.

This is the module the project exists to demonstrate. Everything before it is
defensible engineering that many people can write. A model that is structurally unable
to assert what the rules did not establish is the part worth publishing.

## The shape of the claim

The model does not decide. It does not weigh. It does not add context it happens to
know about Tornado Cash or OFAC from pretraining. It restates, in readable prose, facts
that are already in the Case, and it attributes each one. Anything else is a defect,
and the defect is caught deterministically rather than hoped against.

## Modules

- `narrate/prompt.py` — builds the prompt from Case + Verdict. **Only evidence present
  in the Case may enter the prompt.** If a rule returned UNKNOWN, the prompt carries the
  UNKNOWN and its gaps, never a silent omission.
- `narrate/verify.py` — deterministic post-generation checks. Pure functions, same
  boundary rule as `gate/`: testable with hand-written strings and dicts, importing
  nothing from `ingest/` or `enrich/`.
- `narrate/model.py` — local inference adapter. Runs against the existing local stack on
  the RTX 5070. No cloud, no API key, no fallback to a hosted model — if the model is
  unavailable that is an error, never a quiet substitution.
- `narrate/__main__.py` — CLI.

## Verification — the deterministic part

Run on the generated text, every time, before it is shown to anyone:

1. **Citation existence.** Every transaction-hash-shaped token in the output must appear
   in the Case's evidence. Any hash that does not is a fabrication.
2. **Address existence.** Every address-shaped token must appear in the Case.
3. **Verdict fidelity.** The narrative must state the gate's verdict and no other. A
   narrative describing a FLAGGED subject as clean, or vice versa, fails.
4. **Confidence fidelity.** Where confidence is low, the narrative must say so. Silence
   about thin coverage is the failure this whole project is about.
5. **Uncited assertion.** Any sentence making a factual claim about the subject's
   activity without a citation fails. [Inference] This check is heuristic where the
   others are exact — sentence classification is not a solved problem — so it must be
   implemented conservatively, flag rather than reject, and its false-positive rate must
   be reported in `eval/` rather than assumed low.

Checks 1–4 are exact and reject. Check 5 flags for review and is measured.

## Failure handling — fail closed, visibly

On verification failure: regenerate, up to `MAX_NARRATION_ATTEMPTS`. If every attempt
fails, emit a **deterministic template narrative** built from the Verdict with no model
involvement, and mark it clearly as such in the output and in storage.

The system therefore always produces a case file. That case file is always either
model-written-and-verified or template-generated-and-labelled, and it is never ambiguous
which. A silent fallback would be worse than no fallback at all.

## Storage

Narratives are stored against their verdict, with: which attempt succeeded, which checks
ran, which failed on earlier attempts, the model and quantisation used, and whether the
output is model-generated or template. A narrative that cannot be traced to the exact
Case and model that produced it is not evidence.

## CLI

```
python -m narrate write --verdict-id N [--format text|json|markdown]
python -m narrate verify --narrative-id N     # re-run checks on stored output
```

## eval/ — the numbers that fill the README

This is where the project stops being a demo. Run narration across every stored verdict
and report:

- **Citation precision** — cited hashes that exist in the Case, over all cited hashes.
  Target is 100%; anything less is a hallucination and must be shown, not smoothed.
- **First-attempt pass rate** — how often the model produces verifiable output without
  a retry.
- **Fallback rate** — how often the template path is reached.
- **Finding coverage** — proportion of the Verdict's FLAGs that appear in the narrative.
  A narrative that cites perfectly but omits a finding is a different failure, and
  checks 1–4 would not catch it.
- **Check-5 false-positive rate** — hand-adjudicate a sample and report it honestly.

Report these per model and per quantisation. Whether a 7B at Q4 is good enough for this
job is an empirical question with a publishable answer, and nobody has published it for
this task.

## Design constraints

- **The prompt is evidence, not instruction dressing.** No "you are a helpful compliance
  analyst" persona padding. The Case, the Verdict, and a statement of what may and may
  not be said.
- **No pretrained knowledge may enter the output.** The model may not explain what
  Tornado Cash is, what OFAC does, or what a mixer implies. That is context the reader
  has and the Case does not contain. If it appears, it is an uncited assertion.
- **Determinism where possible.** Fixed seed, temperature recorded, so a narrative is
  reproducible from its stored inputs. Where the stack cannot guarantee it, say so in
  the README rather than implying reproducibility that isn't there.
- **Thresholds** — `MAX_NARRATION_ATTEMPTS` and any others — named constants with
  reasoning comments, added to the README table.

## Acceptance criteria

1. `narrate/verify.py` is tested with hand-written strings and dicts only; AST check
   confirms it imports nothing from `ingest/` or `enrich/`.
2. A narrative containing a fabricated transaction hash is rejected by check 1.
   Demonstrate with a real generation or an injected string, and show the rejection.
3. A narrative for a FLAGGED subject that omits the confidence statement is rejected by
   check 4.
4. All stored verdicts are narrated; `eval/` reports every metric above with real
   numbers.
5. The fallback path is demonstrated: force failure, show the template narrative and its
   marking in both output and storage.
6. One end-to-end run on an OFAC subject, from address to finished case file, shown in
   the README in full.
7. `pytest` passes offline. Model-dependent tests are marked and skipped when the model
   is unavailable, and the suite must still pass without it.

## Out of scope

Solana, any UI, fine-tuning, prompt-optimisation experiments beyond what `eval/` needs
to produce the table.
