-- AI stack spend.
--
-- Blanco runs a lot of models across a lot of providers and had no single
-- number for what it costs. Usage-billed APIs are the reason: a $20/mo
-- subscription is visible, but four metered APIs each quietly billing $8 are
-- not, and on a tight budget that gap matters.
--
-- Seeded with the providers that are verifiably configured in the workspace
-- (their API keys appear in project configs) plus the tools whose billing mail
-- the categorizer already recognises. Costs ship at 0 — the same rule migration
-- 003 set. What he pays is his to enter; guessing it would produce a total that
-- looks authoritative and is wrong.

CREATE TABLE ai_services (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE,
    provider     TEXT    NOT NULL DEFAULT '',
    kind         TEXT    NOT NULL DEFAULT 'api'
                 CHECK (kind IN ('api', 'subscription', 'tool', 'infra')),
    -- 'usage' means metered and variable: cost is whatever was last recorded
    -- for the month rather than a fixed recurring charge.
    billing      TEXT    NOT NULL DEFAULT 'usage'
                 CHECK (billing IN ('monthly', 'annual', 'usage', 'free')),
    cost         REAL    NOT NULL DEFAULT 0,
    currency     TEXT    NOT NULL DEFAULT 'USD',
    is_active    INTEGER NOT NULL DEFAULT 1,
    billing_email TEXT   NOT NULL DEFAULT '',  -- domain the invoices arrive from
    notes        TEXT    NOT NULL DEFAULT '',
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_ai_services_active ON ai_services (is_active, name);

-- Monthly observations for the metered providers, so the trend is real rather
-- than a single number that gets overwritten. `period` is 'YYYY-MM'.
CREATE TABLE ai_spend_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id INTEGER NOT NULL REFERENCES ai_services (id) ON DELETE CASCADE,
    period     TEXT    NOT NULL,
    amount     REAL    NOT NULL,
    source     TEXT    NOT NULL DEFAULT 'manual',
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (service_id, period)
);

INSERT INTO ai_services (name, provider, kind, billing, billing_email, notes) VALUES
('Anthropic API',  'Anthropic',  'api', 'usage', 'anthropic.com',
 'Claude API. Key configured in the workspace.'),
('OpenAI API',     'OpenAI',     'api', 'usage', 'openai.com',
 'Key configured in the workspace.'),
('OpenRouter',     'OpenRouter', 'api', 'usage', 'openrouter.ai',
 'Multi-model router. Key configured in the workspace.'),
('DeepSeek API',   'DeepSeek',   'api', 'usage', 'deepseek.com',
 'Used by repo-auditor for the architecture/quality pass.'),
('NVIDIA Inference', 'NVIDIA',   'api', 'usage', 'nvidia.com',
 'SkillSpector safety scan in repo-auditor.');
