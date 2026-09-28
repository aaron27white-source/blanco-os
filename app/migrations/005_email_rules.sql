-- Importance corrections.
--
-- When the categorizer scores a message wrong, Blanco corrects it here and the
-- correction sticks for every future message that matches. Rules are keyed by
-- who sent it or what it was filed as — never by message id, because messages
-- are transient and the same offender keeps coming back.
--
-- Precedence at classification time: sender > domain > category.

CREATE TABLE email_rules (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scope               TEXT NOT NULL CHECK (scope IN ('sender', 'domain', 'category')),
    match_value         TEXT NOT NULL,                 -- stored lowercased
    importance          INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 5),
    note                TEXT NOT NULL DEFAULT '',
    -- What the agent had said when Blanco overruled it. Kept so the correction
    -- is auditable and so we can show "agent said 4, you said 1".
    original_importance INTEGER,
    hits                INTEGER NOT NULL DEFAULT 0,    -- times it has been applied
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (scope, match_value)
);

CREATE INDEX idx_email_rules_scope ON email_rules(scope, match_value);
