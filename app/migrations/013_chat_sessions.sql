-- The Command Deck console was one thread per engine — switching engines
-- switched what you saw, but there was never more than one conversation per
-- engine to switch between. This adds actual terminal-style sessions: named,
-- creatable, switchable, independent histories that can mix engines over
-- their lifetime (ask OpenClaw, then Claude, in the same session).

CREATE TABLE chat_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL,
    engine TEXT NOT NULL DEFAULT 'openclaw',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Every row written before this migration belongs here, so existing
-- conversations don't disappear behind a session filter on upgrade.
INSERT INTO chat_sessions (id, label, engine) VALUES (1, 'Main', 'openclaw');

ALTER TABLE chat_messages ADD COLUMN session_id INTEGER NOT NULL DEFAULT 1;
