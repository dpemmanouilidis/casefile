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
