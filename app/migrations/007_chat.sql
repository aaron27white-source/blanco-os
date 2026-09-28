-- Messenger.
--
-- One timeline holding both sides of the conversation *and* the OS's own
-- notifications, so "talk to Sweet Jones" and "tell me what happened" are the
-- same surface rather than two places to check.
--
-- An agent turn takes ~70s, so a reply is written as a `pending` row up front
-- and filled in by a background worker. The UI polls that row.

CREATE TABLE chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    role        TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    agent       TEXT NOT NULL DEFAULT 'main',      -- which agent, for sub-agent chats
    body        TEXT NOT NULL DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'done'       -- pending | done | failed
                CHECK (status IN ('pending', 'done', 'failed')),
    error       TEXT NOT NULL DEFAULT '',
    run_id      TEXT NOT NULL DEFAULT '',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    severity    TEXT NOT NULL DEFAULT '',          -- system rows only
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_chat_recent ON chat_messages(id DESC);
CREATE INDEX idx_chat_pending ON chat_messages(status) WHERE status = 'pending';
