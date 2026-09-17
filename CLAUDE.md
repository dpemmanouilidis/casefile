# casefile

A self-hosted agentic investigation pipeline for on-chain data. Ingest transactions,
enrich and label counterparties, trace flows, apply deterministic rules, and produce
an evidence-cited case file. Runs fully local — no cloud LLM, no paid APIs.

Owner: Dimitrios Panagiotis Emmanouilidis. Python 3.11+. Windows dev machine,
local inference on an RTX 5070.

## Why this exists

This repo is a public portfolio artefact with two audiences:

- Enterprise/automotive AI teams — it reads as agentic architecture with safety
  gating on a private, self-hosted deployment.
- Crypto compliance/analytics teams — it reads as their kind of system built under
  their constraints.

It does not need to be novel. It needs to be finished, running, and publicly
measured. Prefer a working end-to-end path over a complete module.

## Non-negotiable design rules

Violating any of these defeats the point of the project.

1. **No model in `gate/`.** Flagging decisions are made by deterministic rules only.
   Every decision carries a machine-readable reason list. If a rule cannot be
   evaluated, it returns UNKNOWN — never a guess.
2. **The model narrates, it does not decide.** `narrate/` receives only the evidence
   the gate produced. Every factual sentence in a narrative cites a transaction hash
   or an address that appears in that evidence.
3. **Fail closed.** If evidence is missing, incomplete, or a source is unreachable,
   the output says so explicitly. Never write around a gap, never infer a fact that
   is not in the evidence.
4. **Everything local.** No cloud LLM calls. No paid API keys. Free-tier or public
   RPC endpoints only, and the code must run when they are unavailable (cached data
   or recorded fixtures).
5. **Chain-agnostic from day one.** Anything EVM-specific lives behind the client
   interface in `ingest/`. Adding Solana must not require touching `gate/`,
   `narrate/`, or the storage schema.
6. **Measured, not asserted.** Any claim in the README must come from a number that
   `eval/` produced. No claim without a measurement behind it.
7. **No reconstructed output.** No command output, transcript, or quoted result
   appears in the README or any doc unless it was captured from a real run of that
   exact command. Reconstructed, paraphrased, or illustrative output is prohibited —
   including editing a real capture's id or numbers to match a later state, and
   including when the reconstruction would plausibly be identical to a real run.
   If a cited artifact (a database row, a narrative id) changes, re-run the command
   and re-capture; do not hand-edit the old transcript.

## Module boundaries

- `ingest/` — fetch transactions, logs and token transfers for a set of addresses;
  normalise to the storage schema. Knows about chains and RPC. Knows nothing about
  rules or narratives.
- `enrich/` — attach labels to counterparties from public label sets; trace flows N
  hops; compute deterministic signals. Reads storage, writes storage.
- `gate/` — pure functions over enriched data. Input: a case. Output: flagged or not,
  plus a list of reasons with the evidence each rests on. No I/O, no model, no
  network. Must be unit-testable with no fixtures beyond plain dicts.
- `narrate/` — local model turns gate output into an analyst-style narrative.
  Prompt construction and citation verification live here. A narrative that cites a
  hash absent from the evidence is a bug and must fail the citation check.
- `eval/` — hand-labelled cases and a script that reports what the gate caught, what
  the narrative cited, and what it invented. Small from the start, never skipped.

## Tech constraints

- Python throughout. No Rust, no smart contracts, no frontend until milestone three.
- SQLite for storage until it demonstrably hurts. Plain SQL, no ORM.
- Amounts stored as strings, never floats. Timestamps UTC, ISO 8601.
- Tests run offline against recorded fixtures. No test may hit a live RPC.
- Dependencies: standard library first. Justify every new package in the commit
  message.

## Working agreement

- One milestone at a time. Do not start the next module until the current one runs
  end-to-end on real data.
- Public from the first commit. Small commits with honest messages.
- When something cannot be verified, say so in the code comment and in the output —
  do not paper over it.
- If a design rule above blocks a task, stop and say so rather than working around it.

## Current milestone

See `docs/milestone-1.md`.
