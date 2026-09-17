CREATE TABLE IF NOT EXISTS narratives (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict_id          INTEGER NOT NULL,      -- FK to gate's verdicts.id
    source              TEXT NOT NULL,         -- 'model' or 'template' — never ambiguous
    text                TEXT NOT NULL,
    model               TEXT NOT NULL,
    quantization        TEXT NOT NULL,
    think               INTEGER NOT NULL,      -- 0/1
    temperature         REAL NOT NULL,
    base_seed           INTEGER NOT NULL,
    successful_seed     INTEGER,               -- the seed that produced `text`, NULL for template
    num_ctx             INTEGER NOT NULL,
    successful_attempt  INTEGER,               -- NULL for template
    attempts_json       TEXT NOT NULL,          -- every attempt: seed, checks run, what failed
    verification_json   TEXT,                  -- final verify_narrative() result, NULL for template
    generated_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_narratives_verdict ON narratives(verdict_id);
