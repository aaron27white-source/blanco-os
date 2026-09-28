-- Blanco OS core state.
-- Only holds what the vault does NOT already own. Tasks, journal entries and
-- cert roadmap text stay in the vault; this DB holds overlays and OS-native
-- objects (alerts, venture ledger, bridge inbox, signal history).

CREATE TABLE ventures (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    emoji           TEXT NOT NULL DEFAULT '',
    thesis          TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT 'idea',      -- idea|building|piloting|earning|paused|archived
    health          TEXT NOT NULL DEFAULT 'unknown',   -- green|yellow|red|unknown
    monthly_target  REAL NOT NULL DEFAULT 0,
    monthly_actual  REAL NOT NULL DEFAULT 0,
    capital_in      REAL NOT NULL DEFAULT 0,
    next_action     TEXT NOT NULL DEFAULT '',
    vault_path      TEXT NOT NULL DEFAULT '',
    repo_path       TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 100,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE venture_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    venture_id  TEXT NOT NULL REFERENCES ventures(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,                          -- revenue|expense|milestone|note
    amount      REAL NOT NULL DEFAULT 0,
    label       TEXT NOT NULL,
    occurred_on TEXT NOT NULL,                          -- YYYY-MM-DD
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_venture_events_venture ON venture_events(venture_id, occurred_on);

CREATE TABLE alerts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,                        -- module id that raised it
    severity      TEXT NOT NULL DEFAULT 'info',         -- critical|warning|info
    title         TEXT NOT NULL,
    detail        TEXT NOT NULL DEFAULT '',
    action_label  TEXT NOT NULL DEFAULT '',
    action_href   TEXT NOT NULL DEFAULT '',
    dedupe_key    TEXT UNIQUE,
    acknowledged  INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    acked_at      TEXT
);
CREATE INDEX idx_alerts_open ON alerts(acknowledged, severity, created_at);

-- Your Business (work OS) -> Personal OS one-way notification bridge.
CREATE TABLE bridge_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    origin      TEXT NOT NULL DEFAULT 'biz',
    kind        TEXT NOT NULL DEFAULT 'notice',         -- notice|deal|invoice|escalation
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    amount      REAL,
    read        INTEGER NOT NULL DEFAULT 0,
    received_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_bridge_unread ON bridge_messages(read, received_at);

-- Operator-set overrides on top of the markdown cert roadmap.
CREATE TABLE cert_progress (
    cert_slug   TEXT PRIMARY KEY,
    status      TEXT NOT NULL,                          -- not_started|in_progress|passed|failed|dropped
    percent     INTEGER NOT NULL DEFAULT 0,
    started_on  TEXT,
    finished_on TEXT,
    note        TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Daily numbers the OS asks Blanco for (or an agent writes).
CREATE TABLE daily_signals (
    day         TEXT NOT NULL,                          -- YYYY-MM-DD
    metric      TEXT NOT NULL,                          -- deep_work_minutes|applications_sent|...
    value       REAL NOT NULL,
    PRIMARY KEY (day, metric)
);

-- Focus override. Falls back to workspace ACTIVE.md when empty.
CREATE TABLE focus (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    headline   TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    horizon    TEXT NOT NULL DEFAULT 'today',           -- today|week|quarter
    set_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only feed the UI can replay; also what /api/stream pushes.
CREATE TABLE activity_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    module     TEXT NOT NULL,
    verb       TEXT NOT NULL,
    subject    TEXT NOT NULL,
    meta_json  TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_activity_recent ON activity_log(created_at DESC);
