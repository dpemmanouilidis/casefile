# casefile

A self-hosted agentic investigation pipeline for on-chain data. See
[CLAUDE.md](CLAUDE.md) for the project's design rules and
[docs/milestone-3.md](docs/milestone-3.md) for the current milestone spec
(milestones 1 and 2, below, are done; see
[docs/milestone-1.md](docs/milestone-1.md) and
[docs/milestone-2.md](docs/milestone-2.md) for their specs).

## Milestone 1: ingest

Pulls transactions and token transfers for a list of Ethereum addresses via
Alchemy's `alchemy_getAssetTransfers`, normalises them to a chain-agnostic
schema, and writes them to a local SQLite database. Re-running the command is
idempotent — no duplicate rows, no errors.

```
python -m ingest --chain ethereum --addresses addresses.txt [--blocks 5000] [--from-block N --to-block N] [--dry-run] [--db data/casefile.db]
```

- `addresses.txt` — one address per line, `#` comments allowed.
- `--blocks N` — how many blocks of history to walk back from `--to-block`
  or the current chain head (default **5000**, not the 250000 in the
  original milestone spec — `addresses.txt` includes a Binance hot wallet
  with 551k lifetime transactions, and the larger window would exhaust the
  Alchemy free tier). This is still a chain-head-relative default — it has
  not been changed to anchor to each subject's own activity. (`enrich
  expand`, described below, does anchor its per-candidate windows to each
  candidate's own last known block; that fix has not been carried back
  into `ingest` itself.) What *has* changed: a window that comes back with
  zero transactions and zero transfers for an address is no longer silently
  indistinguishable from a genuinely quiet address. Before writing the
  run's row, `ingest_address` probes whether that address has *any*
  transfer activity in `[0, to_block]` — two cheap `maxCount=1`
  `alchemy_getAssetTransfers` calls (ascending, one per from/to role) if
  the address is truly inactive, four (adding the descending pair) if it
  isn't. A hit marks the run `partial` with a note naming the address's
  real active block range, so the window (not the address) is what gets
  blamed; a genuine miss is recorded as `ok` with an explicit
  `verified empty` note. See [ingest/evm.py](ingest/evm.py)
  (`AlchemyClient.activity_bounds`) and
  [ingest/__main__.py](ingest/__main__.py) (`zero_result_note`).
- `--from-block` / `--to-block` — a fixed, reproducible block range,
  overriding `--blocks`. `--to-block` alone still defaults to the current
  chain head.
- `--dry-run` — fetch and normalise, print counts, write nothing to disk.
- `--db PATH` — SQLite file to write to (default `data/casefile.db`).

Requires `ALCHEMY_API_KEY` in a local `.env` file (gitignored, never
committed) or the environment.

### Setup

```
pip install -r requirements.txt
cp .env.example .env   # then fill in ALCHEMY_API_KEY
```

### A real run

```
python -m ingest --chain ethereum --addresses addresses.txt
```

```
chain=ethereum blocks=[25980990, 25985990] addresses=8 dry_run=False
  0x04dba1194ee10112fe6c3207c0687def0e78bacf: 0 transactions, 0 transfers written
  0x0ee5067b06776a89ccc7dc8ee369984ad7db5e06: 0 transactions, 0 transfers written
  0xe0f0cfde7ee664943906f17f7f14342e76a5cec7: 0 transactions, 0 transfers written
  0x0003cec240a1ff499f3aea605fb277be87734040: 0 transactions, 0 transfers written
  0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc: 475 transactions, 726 transfers written
  0xc0397d5f71200102ec9152473f58362f0290fc4d: 11 transactions, 50 transfers written
  0xfc908d18f854f93a3b6423226c79334a0947cbfd: 5 transactions, 13 transfers written
  0x9f5dd7e34d5ae6a27896a9e1b209f0c22da8aa0d: 1 transactions, 3 transfers written
row counts: {'chains': 1, 'addresses': 101, 'transactions': 492, 'transfers': 792, 'ingest_runs': 8}
```

[Unverified] The four addresses that returned zero transactions/transfers
(the two OFAC-sanctioned addresses, the Binance hot wallet, and the Kraken
deposit address) were confirmed against the same Alchemy endpoint directly —
they genuinely have no on-chain activity in this ~5000-block (~17 hour)
window. That does not rule out the labels themselves being stale; it only
means this run's block window doesn't overlap their active period.

Running the same command again with no new blocks mined produces identical
row counts (`chains: 1, addresses: 101, transactions: 492, transfers: 792`)
and a new `ingest_runs` row per address (`ingest_runs: 16`) — one row is
always written per address per run, whether or not it produced new data.

### Tests

```
pytest
```

`pytest` runs the whole repo's suite, not just milestone 1's — **66 tests**
as of this writing (`pytest --collect-only -q`), all offline. Milestone
1's own coverage runs against an in-memory fake JSON-RPC backend
(`tests/fake_rpc.py`); no test in the suite touches the network.

### Known limitation

`transactions()` and `transfers()` are both built on top of
`alchemy_getAssetTransfers`, so the `transactions` table only contains
transactions that produced at least one native or token transfer involving
the queried address. A transaction that reverted with no value movement and
no logs would not appear. This is documented in
[ingest/evm.py](ingest/evm.py) and is an accepted limitation for milestone 1
(tracing value flow), not a bug.

### Per-address counts: the exact definition

**"How many transactions/transfers involve address X" means exactly this
query, against the `transactions` and `transfers` tables respectively:**

```sql
SELECT COUNT(*) FROM transfers
WHERE chain_id = 'ethereum' AND (from_address = :addr OR to_address = :addr);

SELECT COUNT(*) FROM transactions
WHERE chain_id = 'ethereum' AND (from_address = :addr OR to_address = :addr);
```

This is the only definition used for any per-address count reported for
this project from here on. Rule 6 (measured, not asserted) requires that a
number be reproducible from a query — an earlier report used the CLI's own
`len(transactions)` fetch count as a stand-in for "the tx count", which is a
*different* number: it counts every transaction that turned up while
fetching asset transfers *for* that address, including ones where the
address only appears as the sender/recipient of an internal (contract-call)
transfer, not as the outer transaction's `from`/`to`. Those two numbers can
legitimately disagree — e.g. the Tornado.Cash pool shows `0` under the
strict definition above (it is never itself the outer transaction's sender
or recipient — deposits and withdrawals route through relayer/router
contracts) but `726` transfers, because every one of those transfers
genuinely has the pool as the transfer's `from_address` or `to_address`.
That is not a bug or drift between runs; it is two different questions.
Anyone citing a transaction count for this project should use the SQL
above and say so, not a console log line from an ingestion run (which is a
progress indicator, not a measurement).

Full dataset as of the last ingestion (`data/casefile.db`, not committed —
regenerate with the commands in this README). **Transfers is the headline
number** — it's the one that reflects whether an address actually moved
value, since `transfers.from_address`/`to_address` are always the real
counterparties of that value movement. `tx (outer-tx endpoints only)` is a
secondary, stricter count: it's `0` whenever every one of an address's
transfers happened via an internal call from a router/relayer contract (the
address was never itself the top-level transaction's sender or recipient) —
that is expected for contracts like the Tornado.Cash pool, not a sign the
pipeline missed anything:

| subject | address | transfers (headline) | tx (outer-tx endpoints only) |
|---|---|---:|---:|
| OFAC sanctioned 1 | `0x04dba1194ee10112fe6c3207c0687def0e78bacf` | 73 | 71 |
| OFAC sanctioned 2 | `0x0ee5067b06776a89ccc7dc8ee369984ad7db5e06` | 317 | 312 |
| Binance 12 (deprecated) | `0xe0f0cfde7ee664943906f17f7f14342e76a5cec7` | 4,185 | 3,241 |
| Kraken deposit | `0x0003cec240a1ff499f3aea605fb277be87734040` | 163 | 153 |
| Binance: Hot Wallet 20 (active) | `0xf977814e90da44bfa03b6295a0616a897441acec` | 53 | 9 |
| Tornado.Cash pool | `0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc` | 726 | 0 |
| control 1 | `0xc0397d5f71200102ec9152473f58362f0290fc4d` | 50 | 10 |
| control 2 | `0xfc908d18f854f93a3b6423226c79334a0947cbfd` | 13 | 5 |
| control 3 | `0x9f5dd7e34d5ae6a27896a9e1b209f0c22da8aa0d` | 3 | 1 |

Overall: `chains: 1, addresses: 2736, transactions: 4331, transfers: 5581,
ingest_runs: 27`, `0` orphan transfers (every transfer's `tx_hash` has a
matching row in `transactions`).

### Out of scope for this milestone

Labels, tracing, rules, the model, any UI, Solana, performance work.

## Milestone 2: enrich (labels and tracing)

See [docs/milestone-2.md](docs/milestone-2.md) for the full spec.

### Part A — labels and is_contract

```
python -m enrich load-labels [--dir enrich/labels] [--db data/casefile.db]
python -m enrich compute-is-contract [--db data/casefile.db]
```

`load-labels` reads every `*.csv` under `enrich/labels/`, in two distinct
tiers:

- **Standard tier** (`chain_id,address,label,category,source,retrieved`) —
  hand-sourced, high confidence, category baked into the file itself:
  [labels.csv](enrich/labels/labels.csv) (the nine `addresses.txt`
  subjects) and [ofac_sdn_eth.csv](enrich/labels/ofac_sdn_eth.csv) (120
  Ethereum addresses extracted from Treasury's official SDN Advanced XML
  export — a one-time bulk download, not scraped; see the file's header
  and [scripts/extract_ofac_eth.py](scripts/extract_ofac_eth.py)).
- **Bulk tier** (`chain_id,address,entity,source,retrieved` — no
  `category` column) —
  [etherscan_labels_bulk.csv](enrich/labels/etherscan_labels_bulk.csv),
  24,859 addresses across all ~420 per-entity CSVs from
  [github.com/brianleect/etherscan-labels](https://github.com/brianleect/etherscan-labels)
  (commit `923aba7`, third-party Etherscan scrape, **unverified**; see the
  file's header). This tier carries an `entity` name, not a category —
  category is resolved at *load time* against
  [entity_categories.csv](enrich/labels/entity_categories.csv), a small
  hand-maintained mapping. An entity with no mapping resolves to
  `unknown`, never a guess, so a `custody_change` in tracing (below) can
  only ever fire on an entity someone deliberately classified.

Both tiers detect automatically by header shape (`is_bulk_tier_file`), so
`load-labels` needs no flag to tell them apart. A malformed `category`
(in either the standard file or `entity_categories.csv`) rejects the whole
file before writing anything (rule 3, fail closed) rather than writing the
good rows ahead of the bad one. Loading is idempotent per file: re-running
a file replaces exactly the rows whose `source` it declares and leaves
every other file's rows untouched — for the bulk tier this also means
editing `entity_categories.csv` and reloading is how a new classification
decision takes effect, with no need to regenerate the 25k-row bulk file.

**How `entity_categories.csv` gets populated**: not by classifying ~420
entities blind. Ranked the database's counterparties by fan-out (distinct
local counterparties, not value — value is the wrong signal for finding
custody boundaries; a mixer or exchange is identifiable by how many
different addresses converge on it, not by transaction size) and joined
the top 50 against the bulk file, mapping only entities that actually
showed up: `binance`, `bitfinex`, `kraken` → `exchange`, `tornado-cash` →
`mixer`. Left `old-contract`, `blocked`, `stablecoin`,
`ofac-sanctions-lists`, and `dex` unmapped — `dex` in particular is a
deliberate exclusion, not an oversight: a DEX router swap stays fully
on-chain and traceable, which is not what `custody_change` exists to flag
(a CEX or mixer breaks the on-chain link; a DEX doesn't).

`compute-is-contract` sets `addresses.is_contract` via `eth_getCode`,
one RPC call per address that doesn't have it set yet (0 calls on a
re-run).

Real run: **34/2763 addresses labelled, 2729 unknown** (idempotent — a
second `load-labels` run reports the same numbers). 217/2763 addresses
are contracts (27 `eth_getCode` calls needed after the `expand` round
below added new addresses; 0 needed on a subsequent re-run — every
address already has `is_contract` set, confirmed directly against the
`addresses` table).

Of the top 50 counterparties by fan-out, **9/50 now carry a label**
(6 `exchange`, 1 `mixer`, 3 `unknown` from the deliberately-unmapped
entities above). The number that actually matters for tracing: re-running
the OFAC-subject trace from the tracing section below picked up **2
hop-1 edges newly marked `custody_change`** (both into the
now-Kraken-labelled `0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0`) that
previously would have been silently traced through as ordinary
counterparties — exactly the false-continuation failure mode this
labelling round exists to close.

### Part B — tracing

```
python -m enrich trace --chain ethereum --address 0x... --hops N [--direction out|in|both] [--min-value RAW] [--max-fanout 50] [--db data/casefile.db]
python -m enrich show --trace-id N [--format text|json]
python -m enrich expand --chain ethereum --address 0x... [--top 20] [--blocks 5000]
```

`trace` walks the local `transfers` table breadth-first from a subject and
makes **zero RPC calls** — anything not already ingested is simply not in
the graph, and the trace says so explicitly rather than looking like a
verified dead end. `--min-value` is a raw base-unit integer threshold, not
normalised across assets (comparing 1 ETH to 1 USDT would require a price
we don't have and won't fabricate) — it's most meaningful when the
transfers being compared share an asset.

Expansion stops at a node for exactly one of four reasons, recorded as
`terminal_reason` on the edge:

- **`hop_limit`** — the requested `--hops` was reached.
- **`custody_change`** — the node is labelled `exchange` or `mixer`; the
  on-chain link breaks there (a domain rule, not a data gap).
- **`not_ingested`** — the node has no local transfers of its own, so the
  path is unknown rather than ended. This must never be silently
  indistinguishable from a real leaf: with `--direction both`, the edge
  that reaches a node always touches that node, so the check explicitly
  excludes the incoming edge itself — otherwise every node would look
  "ingested" purely because we happened to see the one transfer that led
  to it, and `not_ingested` could never fire.
- **`fan_out_cap`** — the node has more than `--max-fanout` (default 50)
  distinct counterparties at that hop; the cap and true count are recorded
  in the run's `note` (`trace_edges` has no room for that detail, only the
  fixed-enum `terminal_reason`).

Two real traces, run against the live database with the network hard-blocked
at the socket layer (stronger than physically disconnecting it — even
loopback connects raise) to prove the zero-RPC claim:

```
python -m enrich trace --chain ethereum --address 0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc --hops 2 --direction out --max-fanout 300
  trace #14: 502 edge(s) recorded, status=ok
    not_ingested: 502 edge(s)
```

The Tornado.Cash pool's direct counterparties are almost entirely
addresses we've never separately ingested — every one of those 502 edges
is honestly marked `not_ingested`, not silently dropped.

```
python -m enrich trace --chain ethereum --address 0x0ee5067b06776a89ccc7dc8ee369984ad7db5e06 --hops 2 --direction both --max-fanout 400
  trace #16: 406 edge(s) recorded, status=ok
    custody_change: 2 edge(s)
    hop_limit: 89 edge(s)
    not_ingested: 267 edge(s)
```

This OFAC subject has 280 direct counterparties: 2 edges (both into the
now-Kraken-labelled `0x267be1c1d684f78cb4f6a176c4911b741e4ffdc0`) stop
with `custody_change` and are correctly *not* traced further; 48 other
counterparties had further local activity of their own and expanded to 89
hop-2 edges (`hop_limit`); the remaining 267 had no activity beyond the
edge that reached them and are marked `not_ingested`. Cross-checked
directly against the data: every one of those 267 has, at most, only the
duplicate transfer(s) that are the incoming edge itself — never a third
party — so none of them are silently mis-marked.

#### `expand`, and a second dormant-window bug it exposed

Ran `expand --top 20 --blocks 5000` for both OFAC subjects (40 candidate
addresses total). First attempt: 162 RPC calls, **0 rows written** — every
candidate's ranking came from its full known history (some back to 2019),
but the ingest window was `[latest_block - 5000, latest_block]`, the same
head-relative default as `ingest`. Same class of bug fixed for `ingest` in
milestone 1, reappearing in `expand`. Fixed by anchoring each candidate's
window to its own last known interaction block instead of chain head
(same fix shape, applied to the one place that still had the old
behaviour). Re-running the fixed version against the same 40 candidates:
322 RPC calls, real data for 39/40.

Before/after, both OFAC traces:

| | hop-1 `not_ingested` | hop-2 edges (`hop_limit`) | total edges |
|---|---:|---:|---:|
| OFAC subject 1 (71 lifetime txns) — before | 60 | 315 | 388 |
| OFAC subject 1 — after | 44 | 340 | 413 |
| OFAC subject 2 (312 lifetime txns) — before | 277 | 71 | 388 |
| OFAC subject 2 — after | 267 | 89 | 406 |

**26 hop-1 nodes moved out of `not_ingested`** across both subjects (16 +
10) — real counterparty history that is now part of the local graph
instead of an acknowledged unknown. Total RPC calls for the whole
`expand` exercise, including the wasted first attempt: **484** (162 +
322). Alchemy doesn't expose compute-unit cost in the JSON-RPC response
(checked directly — no `X-*` CU header on any response), so this is a
call count, not an authoritative CU figure; check the Alchemy dashboard
for the exact CU total if that matters. At this rate (roughly 8 RPC
calls per address ingested) a much larger expansion is a call-count
question, not obviously a CU-budget one — worth re-checking if a future
round expands hundreds of addresses rather than dozens.

`expand` ranks a subject's direct counterparties by total native ETH value
transferred (same methodology as the per-address counterparty ranking
above — raw ETH only, no cross-asset conversion), excludes any already
labelled `exchange` or `mixer`, prints the addresses it's about to ingest
before making any RPC call, then calls the existing milestone-1 ingest for
the top N.

### Tests

Still the same full-suite `pytest` run described in Milestone 1 above.
`tests/test_trace.py` covers each of the four termination reasons against
fixture data, including a regression test for the
`not_ingested`-vs-`both`-direction edge case above, and
`tests/test_expand.py` covers the window-anchoring fix described above.

## Milestone 3: signals and the gate

See [docs/milestone-3.md](docs/milestone-3.md) for the full spec.

### Part A — signals

`enrich/signals.py` computes five pure functions over a Case (subject,
labels, trace edges — see `enrich/case.py`): `sanctioned_exposure`,
`mixer_interaction`, `pass_through`, `counterparty_concentration`,
`unlabelled_share`. Every signal reports `complete` and `gaps` scoped to
exactly what it depends on, not the whole trace — a gap three hops away
never marks a direct-transfer signal incomplete. `sanctioned_exposure`
spans hop 1..N, so its completeness is reported per hop distance instead
of one flat flag.

### Part B — the gate

```
python -m gate assess --chain ethereum --address 0x... [--hops 2] [--max-fanout 50] [--format text|json] [--db data/casefile.db]
python -m gate replay --verdict-id N
```

`gate/rules.py` has five rules — `subject_sanctioned`, `sanctioned_direct`,
`sanctioned_indirect`, `mixer_outbound`, `rapid_pass_through` — each a pure
function from Case to `{rule, outcome, reason, evidence, signal}`, no
database, no network, testable with a hand-written dict. `subject_sanctioned`
is a separate finding from `sanctioned_direct`/`sanctioned_indirect`: the
subject's own label is never conflated with exposure to someone else's.

**Interpretation policy**: a finding FLAGs regardless of gaps outside its
own scope — a verified sanctioned transfer is a positive fact, and an
un-ingested node elsewhere doesn't unfind it. Where gaps exist in the
relevant scope, the reason says exposure may be greater than measured.
Nothing found + scope complete → PASS. Nothing found + scope incomplete →
UNKNOWN. This is fail-closed's actual shape here: the dangerous direction
is claiming clean without having looked, never claiming flagged on
evidence genuinely found.

**Verdict**: any `FLAG` → `FLAGGED`, regardless of `UNKNOWN`s elsewhere.
No `FLAG` and any `UNKNOWN` → `UNASSESSABLE`. No `FLAG` and all `PASS` →
`CLEAR` — *unless* confidence is `low`, which forces `UNASSESSABLE`
instead; `CLEAR` with `low` confidence is impossible by construction
(enforced in `gate/verdict.py`, not left to chance).

**Confidence** is a separate field on the verdict, not a rule — a
statement about the case's evidence coverage, computed by
`gate.rules.compute_confidence` and never competing with a finding for
precedence: `{level: "low"|"high", reason, unlabelled_share,
total_counterparties, hop1_gap_fraction}`.

Verdicts are stored in a `verdicts` table with the full case and rule
list as JSON, so `gate replay --verdict-id N` recomputes everything from
stored evidence — zero database queries beyond loading that one row, zero
network — and asserts the recomputed rules, confidence, and verdict are
byte-identical to what was stored.

### Thresholds

Every named threshold/window/retry constant in the codebase, its value,
and why. (Design constraint from `docs/milestone-3.md`: every one of
these will be questioned by a reader, and none should require reading the
code to find it.)

| constant | file | value | reason |
|---|---|---:|---|
| `DEFAULT_BLOCKS` | `ingest/__main__.py` | 5000 | keeps free-tier Alchemy usage bounded; `addresses.txt` includes a Binance hot wallet with 551k lifetime transactions, and a much larger window would exhaust the free tier on a single address |
| `MAX_RETRIES` | `ingest/evm.py` | 5 | enough to ride out a short burst of free-tier rate limiting without one flaky call turning an entire address into a partial run |
| `INITIAL_BACKOFF_SECONDS` | `ingest/evm.py` | 1.0 | doubles each retry (1s, 2s, 4s, 8s, 16s, ~31s total) — fast enough not to stall an interactive run, long enough that a real rate-limit window usually clears before `MAX_RETRIES` is exhausted |
| `DEFAULT_MAX_FANOUT` | `enrich/trace.py` | 50 | stops expansion through a busy intermediate node partway through a trace, bounding cost — an ordinary counterparty rarely has more than a few dozen of its own direct counterparties, and a node this "hot" is worth a dedicated look, not silent expansion. **Never applied to the subject itself** (hop 0) — a real subject (an OFAC-listed entity, an exchange wallet) can have thousands of direct counterparties, and capping the subject produced zero edges and turned every rule `UNKNOWN` the first time this was tried for real |
| `PASS_THROUGH_WINDOW_BLOCKS` | `enrich/signals.py` | 100 (~20 min at ~12s/block) | catches same-session forwarding without treating unrelated later reuse of an address as pass-through |
| `PASS_THROUGH_TOLERANCE_PCT` | `enrich/signals.py` | 0.05 (5%) | forwarded amount within 5% of received allows for the outbound leg being shaved by gas fees, without loosening enough to catch genuinely unrelated transfers of a similar size |
| `UNLABELLED_MIN_SAMPLE` | `enrich/signals.py` | 10 | below this many direct counterparties, an unlabelled-share percentage is noise, not a confidence measure — 0% unlabelled over 2 counterparties says almost nothing about how well-labelled this subject's activity generally is |
| `RAPID_PASS_THROUGH_MIN_MATCHES` | `gate/rules.py` | 1 | even a single received-then-forwarded match within the window/tolerance is a real pass-through event — layering typically shows as isolated instances per subject rather than a repeated pattern, so requiring more than one would miss the common case |
| `UNASSESSABLE_UNLABELLED_THRESHOLD` | `gate/rules.py` | 0.5 (50%) | more than half of direct counterparties unlabelled means we cannot tell whether this subject mostly associates with clean or risky parties — the natural "we know less than we don't" cutoff |
| `UNASSESSABLE_GAP_THRESHOLD` | `gate/rules.py` | 0.5 (50%) | same rationale as above, applied to trace coverage (hop-1 `not_ingested`/`fan_out_cap`) instead of label coverage |

### Tests

`pytest` — 95 tests, all offline. `tests/test_rules.py` is entirely
hand-written dicts, no `ingest`/`enrich` imports, and includes a
mechanical (AST-based, not substring-grep) check that `gate/rules.py`
itself imports nothing from `ingest`, `enrich`, or `sqlite3`.
