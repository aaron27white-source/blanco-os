-- Work / break timer.
-- Sessions live server-side rather than in the browser so a reload doesn't lose
-- the countdown, and so agents can see whether Blanco is heads-down.

CREATE TABLE focus_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kind         TEXT NOT NULL,                       -- work | break
    label        TEXT NOT NULL DEFAULT '',
    planned_secs INTEGER NOT NULL,
    started_at   TEXT NOT NULL DEFAULT (datetime('now')),
    ends_at      TEXT NOT NULL,
    stopped_at   TEXT,
    status       TEXT NOT NULL DEFAULT 'running'      -- running | completed | cancelled
);

-- Only one session may be running at a time; this makes that structural rather
-- than a race in application code.
CREATE UNIQUE INDEX idx_one_running_session
    ON focus_sessions ((1)) WHERE status = 'running';

CREATE INDEX idx_focus_sessions_day ON focus_sessions(started_at);
