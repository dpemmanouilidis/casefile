CREATE TABLE IF NOT EXISTS verdicts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    verdict      TEXT NOT NULL,
    case_id      INTEGER NOT NULL,      -- trace_id the case was built from
    case_json    TEXT NOT NULL,         -- the full Case, so a verdict is reproducible offline
    rules_json   TEXT NOT NULL,         -- the full rule list, including PASS and UNKNOWN
    generated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_verdicts_subject ON verdicts(chain_id, subject);
