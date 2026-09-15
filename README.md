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

### Out of scope for this milestone

Labels, tracing, rules, the model, any UI, Solana, performance work.
