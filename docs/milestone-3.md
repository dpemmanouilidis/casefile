# Milestone 3 — signals and the gate

**Goal:** turn an enriched subject into a decision. Deterministic signals computed from
the database, deterministic rules that consume them, and a verdict carrying a
machine-readable reason list where every reason points at the evidence that produced it.

No model. This is the module the whole project exists to demonstrate.

## The boundary that matters

`CLAUDE.md` rule 1 says `gate/` contains no model, no I/O and no network. That is
enforced structurally, not by discipline:

- `enrich/case.py` — assembles a **Case**: a plain dict containing the subject, its
  labels, its trace edges, and its computed signals. This module does the database
  reading.
- `gate/` — pure functions from Case to Verdict. Every function in `gate/` must be
  callable with a hand-written dict and no database, no fixtures, no patching. If a
  `gate/` test needs a database, the boundary has been broken.

A Case is serialisable to JSON and is the same artefact milestone 4 hands to the model
as evidence. Design it as evidence from the start.

## Part A — signals

Computed in `enrich/signals.py`, stored on the Case, never inferred.

Each signal returns a value **and** a completeness marker. A signal computed over a
trace containing `not_ingested` nodes is incomplete, and must say so — the gate needs
to distinguish "no exposure found" from "exposure not measurable".

```python
{
  "name": "sanctioned_exposure",
  "value": ...,
  "complete": false,
  "gaps": ["3 hop-1 nodes not_ingested", "1 node fan_out_cap"],
  "evidence": ["0xabc...", "0xdef..."]   # tx hashes or addresses, always
}
```

Five signals, all computable from what milestone 2 produced:

1. `sanctioned_exposure` — value received from or sent to any address labelled
   `sanctioned`, direct and via trace paths, with hop distance recorded. Evidence: the
   tx hashes on the path.
2. `mixer_interaction` — transfers to or from any address labelled `mixer`, with
   direction and count. Direction matters: receiving from a mixer is a different fact
   from sending to one.
3. `pass_through` — value received and forwarded on within a short window, where the
   forwarded amount is close to the received amount. Parameters (window, tolerance)
   are explicit constants in the module, not magic numbers inline.
4. `counterparty_concentration` — share of total value flowing to the single largest
   counterparty, and the count of distinct counterparties.
5. `unlabelled_share` — proportion of counterparties with no label. Not a risk signal;
   a **confidence** signal. A subject whose counterparties are 95% unknown cannot be
   assessed, and the gate must be able to see that.

## Part B — the gate

`gate/rules.py`. Each rule is a pure function returning one of three outcomes:

- `FLAG` — the rule's condition is met, with the evidence that met it.
- `PASS` — the condition is not met and the data needed to evaluate it was complete.
- `UNKNOWN` — the data needed was incomplete. **Never a guess, never a default to PASS.**

```python
{
  "rule": "sanctioned_direct",
  "outcome": "FLAG",
  "reason": "received value directly from an address on the OFAC SDN list",
  "evidence": ["0x<txhash>", "0x<address>"],
  "signal": "sanctioned_exposure"
}
```

Five rules to start:

1. `sanctioned_direct` — direct transfer to or from a `sanctioned` address.
2. `sanctioned_indirect` — a `sanctioned` address within N hops, N recorded in the
   reason. UNKNOWN if any node on the shortest candidate path is `not_ingested`.
3. `mixer_outbound` — value sent to a `mixer`.
4. `rapid_pass_through` — `pass_through` signal above threshold.
5. `unassessable` — `unlabelled_share` above threshold, or more than half of hop-1
   nodes `not_ingested`. This rule fires as FLAG on the *case*, not the subject: it
   says the evidence is too thin to judge, which is a finding in itself.

### Verdict

```python
{
  "subject": "0x...",
  "verdict": "FLAGGED" | "CLEAR" | "UNASSESSABLE",
  "rules": [ ...every rule, including PASS and UNKNOWN... ],
  "generated_at": "...",
  "case_id": ...
}
```

`CLEAR` requires every rule to return PASS. **A single UNKNOWN makes the verdict
`UNASSESSABLE`, never `CLEAR`.** This is the fail-closed rule made concrete, and it is
the single most important line in the milestone.

Store verdicts in a `verdicts` table with the case they were computed from, so a verdict
is reproducible from stored evidence rather than only re-derivable by re-running.

## CLI

```
python -m gate assess --chain ethereum --address 0x... [--hops 2] [--format text|json]
python -m gate replay --verdict-id N     # recompute from the stored case, assert identical
```

`replay` exists because rule 6 says measured, not asserted: a verdict that cannot be
reproduced byte-for-byte from its stored case is not evidence.

## Design constraints carried forward

- **Never anchor a block window to chain head.** This bug has now appeared twice —
  once in `ingest`, once in `expand`. Any window must be anchored to the subject's own
  known activity unless the caller explicitly asks for recent activity. If milestone 3
  adds any fetching at all, this applies.
- **Thresholds are named constants with a comment explaining the choice**, in one
  module. Every one of them will be questioned by a reader; none of them should require
  reading the code to find.
- **No rule may consult a label without checking its tier.** A hand-sourced label and a
  third-party scrape are different confidence levels, and a FLAG resting on an
  unverified scrape must say so in its reason.

## Acceptance criteria

1. Every function in `gate/` is tested with hand-written dicts only — no database, no
   fixtures, no mocks. Demonstrate by showing the test file imports nothing from
   `ingest/` or `enrich/`.
2. A subject with an incomplete trace produces `UNASSESSABLE`, never `CLEAR`, and the
   verdict lists which rules returned UNKNOWN and why.
3. Both OFAC subjects produce `FLAGGED` with `sanctioned_direct` firing, and the
   evidence hashes in the reason resolve to real rows in `transactions`.
4. At least one control subject produces a verdict, and whatever it is — `CLEAR` or
   `UNASSESSABLE` — the outcome is explained by the rule list rather than asserted.
5. `gate replay` reproduces a stored verdict exactly.
6. Every threshold in the codebase is listed in the README with its value and the
   reason for it.
7. `pytest` passes offline.

## Out of scope

The model, narration, Solana, any UI, anything that writes prose.
