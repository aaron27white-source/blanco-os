-- Metric history and the notification outbox.

-- One row per metric per snapshot. Without this every metric reports
-- trend "unknown" forever, because nothing is ever compared to anything.
CREATE TABLE metric_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    day        TEXT NOT NULL,                 -- YYYY-MM-DD
    metric_key TEXT NOT NULL,
    value      REAL NOT NULL,
    target     REAL,
    health     TEXT NOT NULL DEFAULT 'unknown',
    taken_at   TEXT NOT NULL DEFAULT (datetime('now')),
    -- One snapshot per metric per day; re-running just updates it.
    UNIQUE (day, metric_key)
);
CREATE INDEX idx_metric_history_key ON metric_history(metric_key, day);

-- What we sent, where, and whether it worked. Doubles as the dedupe ledger so
-- a standing alert is not re-sent every sweep.
CREATE TABLE notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     TEXT NOT NULL,                -- discord | hq_inbox
    dedupe_key  TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT NOT NULL DEFAULT '',
    severity    TEXT NOT NULL DEFAULT 'info',
    ok          INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',
    sent_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_notifications_dedupe ON notifications(dedupe_key, sent_at);
