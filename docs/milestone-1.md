# Milestone 1 — ingest (target: Wed 9 Sep, end of day)

**Goal:** one command pulls transactions and token transfers for a list of EVM
addresses into a local SQLite database, normalised to a chain-agnostic schema, and
re-running it is idempotent.

Nothing else. No enrichment, no rules, no model.

## Schema

Single SQLite file, `data/casefile.db`. Plain SQL in `ingest/schema.sql`.

```sql
CREATE TABLE IF NOT EXISTS chains (
    chain_id      TEXT PRIMARY KEY,   -- 'ethereum', 'solana'
    native_symbol TEXT NOT NULL,
    native_decimals INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
    chain_id   TEXT NOT NULL,
    address    TEXT NOT NULL,         -- lowercase for EVM, base58 as-is for Solana
    is_subject INTEGER NOT NULL DEFAULT 0,  -- 1 = we deliberately ingested this one
    first_seen TEXT,                  -- ISO 8601 UTC
    last_seen  TEXT,
    PRIMARY KEY (chain_id, address)
);

CREATE TABLE IF NOT EXISTS transactions (
    chain_id     TEXT NOT NULL,
    tx_hash      TEXT NOT NULL,
    block_number INTEGER NOT NULL,
    block_time   TEXT NOT NULL,       -- ISO 8601 UTC
    from_address TEXT NOT NULL,
    to_address   TEXT,                -- NULL for contract creation
    value_raw    TEXT NOT NULL,       -- string, never float
    fee_raw      TEXT,
    status       TEXT NOT NULL,       -- 'success' | 'failed' | 'unknown'
    method_id    TEXT,                -- EVM: first 4 bytes of calldata, hex
    PRIMARY KEY (chain_id, tx_hash)
);

CREATE TABLE IF NOT EXISTS transfers (
    chain_id      TEXT NOT NULL,
    tx_hash       TEXT NOT NULL,
    transfer_index INTEGER NOT NULL,  -- EVM: log index
    asset_address TEXT,               -- NULL = native asset
    asset_symbol  TEXT,
    asset_decimals INTEGER,
    from_address  TEXT NOT NULL,
    to_address    TEXT NOT NULL,
    amount_raw    TEXT NOT NULL,      -- string, base units
    PRIMARY KEY (chain_id, tx_hash, transfer_index)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id    TEXT NOT NULL,
    address     TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    from_block  INTEGER,
    to_block    INTEGER,
    tx_count    INTEGER,
    status      TEXT NOT NULL,        -- 'running' | 'ok' | 'partial' | 'failed'
    note        TEXT                  -- why it was partial or failed, in plain words
);

CREATE INDEX IF NOT EXISTS idx_tx_from ON transactions(chain_id, from_address);
CREATE INDEX IF NOT EXISTS idx_tx_to   ON transactions(chain_id, to_address);
CREATE INDEX IF NOT EXISTS idx_tr_from ON transfers(chain_id, from_address);
CREATE INDEX IF NOT EXISTS idx_tr_to   ON transfers(chain_id, to_address);
```

Rules: amounts and fees are strings in base units. Symbols and decimals are recorded
as reported by the source and are not trusted downstream — they are display metadata,
not evidence.

## Interface

`ingest/client.py` defines the boundary every chain must satisfy. EVM specifics go in
`ingest/evm.py`; Solana lands later as `ingest/solana.py` without changing callers.

```python
class ChainClient(Protocol):
    chain_id: str

    def latest_block(self) -> int: ...

    def transactions(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transaction]: ...

    def transfers(
        self, address: str, from_block: int | None = None, to_block: int | None = None
    ) -> Iterator[Transfer]: ...
```

`Transaction` and `Transfer` are frozen dataclasses matching the schema columns.
Normalisation happens inside the client — callers never see raw RPC JSON.

## CLI

```
python -m ingest --chain ethereum --addresses addresses.txt --blocks 250000 [--dry-run]
```

- `addresses.txt`: one address per line, `#` comments allowed.
- `--blocks N`: how far back from the current head to walk. Keeps free-tier usage
  bounded and makes runs reproducible enough to compare.
- `--dry-run`: fetch and normalise, print counts, write nothing.
- Writes an `ingest_runs` row per address, always, including on failure.

## Data source

Public or free-tier EVM RPC. Whichever is used, isolate it behind `ChainClient` so it
can be swapped. Handle rate limits with backoff and record a `partial` run rather than
crashing or silently truncating.

## Subject addresses

Pick roughly ten and commit them in `addresses.txt` with a one-line comment each:

- 2–3 exchange hot wallets (source them from Etherscan's public label pages)
- 1–2 sanctioned addresses (source them from the current OFAC SDN list —
  treasury.gov publishes the crypto addresses directly)
- 1 mixer or bridge contract
- 3–4 ordinary active wallets as controls

[Unverified] Do not take specific addresses from memory or from an LLM — copy them
from the source and record where each came from in the comment. A wrong subject
address poisons every measurement downstream.

## Acceptance criteria

Milestone 1 is done when all of these hold:

1. `python -m ingest --chain ethereum --addresses addresses.txt --blocks 250000`
   completes and populates all four tables.
2. Running it twice produces no duplicate rows and no errors.
3. Killing it mid-run and restarting leaves the database consistent; the interrupted
   run is recorded as `partial` with a note.
4. `pytest` passes offline, against recorded fixtures, with no network access.
5. `README.md` states what the command does and shows real row counts from a real run.
6. The repo is public and pushed.

## Out of scope

Labels, tracing, rules, the model, any UI, Solana, performance work.
