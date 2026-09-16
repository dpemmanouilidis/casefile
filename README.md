# casefile

A self-hosted agentic investigation pipeline for on-chain data. See
[CLAUDE.md](CLAUDE.md) for the project's design rules and
[docs/milestone-1.md](docs/milestone-1.md) for the current milestone spec.

## Milestone 1: ingest

Pulls transactions and token transfers for a list of Ethereum addresses via
Alchemy's `alchemy_getAssetTransfers`, normalises them to a chain-agnostic
schema, and writes them to a local SQLite database. Re-running the command is
idempotent — no duplicate rows, no errors.

```
python -m ingest --chain ethereum --addresses addresses.txt [--blocks 5000] [--dry-run] [--db data/casefile.db]
```

- `addresses.txt` — one address per line, `#` comments allowed.
- `--blocks N` — how many blocks of history to walk back from the current
  chain head (default **5000**, not the 250000 in the original milestone
  spec — `addresses.txt` includes a Binance hot wallet with 551k lifetime
  transactions, and the larger window would exhaust the Alchemy free tier).
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

All 14 tests run offline against an in-memory fake JSON-RPC backend
(`tests/fake_rpc.py`) — no test touches the network.

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

`load-labels` reads every `*.csv` under `enrich/labels/` (currently
[labels.csv](enrich/labels/labels.csv), the nine `addresses.txt` subjects,
and [ofac_sdn_eth.csv](enrich/labels/ofac_sdn_eth.csv), 120 Ethereum
addresses extracted from Treasury's official SDN Advanced XML export — a
one-time bulk download, not scraped; see the file's header for provenance
and [scripts/extract_ofac_eth.py](scripts/extract_ofac_eth.py) for the
extraction). A malformed `category` rejects the whole file before writing
anything (rule 3, fail closed) rather than writing the good rows ahead of
the bad one. Loading is idempotent per file: re-running a file replaces
exactly the rows whose `source` it declares and leaves every other file's
rows untouched, so a row removed from a CSV disappears on the next load
instead of accumulating.

`compute-is-contract` sets `addresses.is_contract` via `eth_getCode`,
one RPC call per address that doesn't have it set yet (0 calls on a
re-run).

Real run: **12/2736 addresses labelled, 2724 unknown** — a thin seed, as
expected before the top-25-counterparty labelling round is folded in.
217/2736 addresses are contracts (2736 `eth_getCode` calls on the first
run, 0 on re-run).

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
  trace #5: 502 edge(s) recorded, status=ok
    not_ingested: 502 edge(s)
```

The Tornado.Cash pool's direct counterparties are almost entirely
addresses we've never separately ingested — every one of those 502 edges
is honestly marked `not_ingested`, not silently dropped.

```
python -m enrich trace --chain ethereum --address 0x0ee5067b06776a89ccc7dc8ee369984ad7db5e06 --hops 2 --direction both --max-fanout 400
  trace #6: 439 edge(s) recorded, status=ok
    hop_limit: 122 edge(s)
    not_ingested: 277 edge(s)
```

This OFAC subject has 317 direct counterparties: 40 had further local
activity of their own and expanded to 122 hop-2 edges (`hop_limit`); the
other 277 had none beyond the edge that reached them and are marked
`not_ingested` — verified directly against the data, not asserted.

`expand` ranks a subject's direct counterparties by total native ETH value
transferred (same methodology as the per-address counterparty ranking
above — raw ETH only, no cross-asset conversion), excludes any already
labelled `exchange` or `mixer`, prints the addresses it's about to ingest
before making any RPC call, then calls the existing milestone-1 ingest for
the top N.

### Tests

`pytest` — 39 tests, all offline. `tests/test_trace.py` covers each of the
four termination reasons against fixture data, including a regression test
for the `not_ingested`-vs-`both`-direction edge case above.
