CREATE TABLE IF NOT EXISTS labels (
    chain_id   TEXT NOT NULL,
    address    TEXT NOT NULL,
    label      TEXT NOT NULL,
    category   TEXT NOT NULL,
    source     TEXT NOT NULL,
    retrieved  TEXT NOT NULL,      -- ISO 8601 date
    PRIMARY KEY (chain_id, address, label)
);

CREATE INDEX IF NOT EXISTS idx_labels_address ON labels(chain_id, address);

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
