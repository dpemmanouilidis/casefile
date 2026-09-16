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
