# Milestone 2 — enrich: labels and tracing

**Goal:** label the counterparties already in the database, and trace value flows out
from a subject address across N hops, producing a queryable subgraph that a later gate
can reason over.

No rules, no scoring, no model. Those are milestone 3.

## Scope decision

`CLAUDE.md` places deterministic signals in `enrich/`. They are deferred to milestone 3
and specified alongside the gate, because a signal only has meaning next to the rule
that consumes it. Milestone 2 delivers the two things a signal needs to exist:
who the counterparties are, and how value reached them.

## Part A — labels

Labels are read from a local file committed to the repo. No scraping, no label API, no
network call. Same discipline as `addresses.txt`: everything hand-sourced with
provenance, so the label set is reproducible and diffable.

`enrich/labels/labels.csv`, one row per address:

```csv
chain_id,address,label,category,source,retrieved
ethereum,0x12d66f87a04a9e220743712ce6d9bb1b5616b8fc,Tornado.Cash: 0.1 ETH,mixer,etherscan.io/accounts/label/tornado-cash,2026-09-16
```

`category` is a closed set, and nothing outside it may be written:
`exchange`, `mixer`, `sanctioned`, `bridge`, `contract`, `control`, `unknown`.

Storage — one new table:

```sql
CREATE TABLE IF NOT EXISTS labels (
    chain_id   TEXT NOT NULL,
    address    TEXT NOT NULL,
    label      TEXT NOT NULL,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    retrieved  TEXT NOT NULL,      -- ISO 8601 date
    PRIMARY KEY (chain_id, address, label)
);
```

Loading is idempotent: re-running replaces the file's rows and leaves others alone.
An address with no row is `unknown` — absence is never treated as "clean".

One derived label may be computed rather than sourced, because it is deterministic and
cheap: `is_contract`, from `eth_getCode` returning non-empty. Store it as a separate
boolean column on `addresses`, not as a row in `labels` — it is an observation about the
chain, not a claim about identity.

Seed the file with the nine subjects from `addresses.txt` plus any counterparty you can
source a label for. A thin label set is fine; a wrong one is not.

## Part B — tracing

Tracing runs **entirely over the local database**. It issues no RPC calls. Anything not
already ingested is simply not in the graph, and the trace must say so rather than
implying the path ended.

```
python -m enrich trace --chain ethereum --address 0x... --hops 2 [--min-value ...] [--direction out|in|both]
```

Walk the `transfers` table breadth-first from the subject, recording edges:

```sql
CREATE TABLE IF NOT EXISTS trace_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id    TEXT NOT NULL,
    subject     TEXT NOT NULL,
    hops        INTEGER NOT NULL,
    direction   TEXT NOT NULL,
    min_value   TEXT,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    status      TEXT NOT NULL,     -- 'ok' | 'partial' | 'failed'
    note        TEXT
);

CREATE TABLE IF NOT EXISTS trace_edges (
    trace_id      INTEGER NOT NULL,
    hop           INTEGER NOT NULL,      -- 1 = direct counterparty
    from_address  TEXT NOT NULL,
    to_address    TEXT NOT NULL,
    tx_hash       TEXT NOT NULL,
    transfer_index INTEGER NOT NULL,
    asset_address TEXT,
    amount_raw    TEXT NOT NULL,
    terminal_reason TEXT,                -- why expansion stopped here, or NULL
    PRIMARY KEY (trace_id, hop, tx_hash, transfer_index)
);
```

### Termination rules — the important part

Expansion stops at a node, and `terminal_reason` records which rule fired:

- `hop_limit` — the requested hop count was reached.
- `custody_change` — the node is labelled `exchange` or `mixer`. Tracing through these
  is meaningless: custody changes and the on-chain link breaks. This is a domain rule,
  not an optimisation, and it must be recorded on the edge so a reader sees where the
  trail genuinely ends.
- `not_ingested` — the node has no transfers in the local database, so the path is
  unknown rather than ended. This distinction is the whole point; never let an
  un-ingested node look like a leaf.
- `fan_out_cap` — the node exceeds `--max-fanout` (default 50) distinct counterparties
  at this hop. Record the cap and the true count in the note.

### Expanding the dataset

A separate command, so tracing stays offline and the RPC cost is explicit:

```
python -m enrich expand --chain ethereum --address 0x... --top 20 --blocks 5000
```

Takes the subject's direct counterparties, ranks them by total value transferred,
and calls the existing milestone-1 ingest for the top N. Bounded by construction:
never expands `exchange` or `mixer` nodes, and prints the number of addresses it will
ingest before starting.

## Output

```
python -m enrich show --trace-id N [--format text|json]
```

Prints the subgraph: subject, then each hop with counterparty, label or `unknown`,
total value, and terminal reason where set. Text output is for you; JSON is what
milestone 4 will hand to the model as evidence, so every field must be machine-readable
and every claim traceable to a `tx_hash`.

## Acceptance criteria

1. `labels.csv` loads into the database, re-running changes no row counts, and a
   malformed category is rejected with a clear error rather than written.
2. `is_contract` is set for every address in the `addresses` table, and the RPC calls
   it required are reported.
3. A two-hop trace from the Tornado.Cash pool completes and shows at least one edge
   with `terminal_reason = 'custody_change'` or `'not_ingested'`.
4. A two-hop trace from an OFAC subject completes, and every hop-1 counterparty that
   has no local transfers is marked `not_ingested` — none silently dropped.
5. `enrich trace` makes zero network calls. Prove it by running with the network
   disconnected.
6. `pytest` passes offline; new tests cover each of the four termination reasons with
   fixture data, not live data.
7. README gains a tracing section with a real example trace and its real numbers.

## Out of scope

Rules, scoring, risk signals, the model, any UI, Solana, graph visualisation.
