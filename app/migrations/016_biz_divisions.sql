-- Your Business becomes three divisions.
--
-- Until now Your Business was a single row in `ventures`, sitting beside `northwind`
-- and `electronics-export` as though the three were peers. They are not: the
-- electronics work *is* Your Business. This migration makes the hierarchy real.
--
-- Divisions own ventures; ventures already own venture_events. So a division's
-- revenue is a roll-up of a ledger that already exists — nothing is recorded
-- twice, and money_service stays the only thing that writes an amount.
--
-- Following the rule migration 003 set: only identity is seeded. No stage, no
-- target, no health, no next action — those are judgement calls and they are
-- Blanco's to make through PATCH /api/biz/divisions/{id}. A monthly_target
-- of 0 means "not set yet", not "expected to earn nothing".

CREATE TABLE biz_divisions (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    emoji           TEXT NOT NULL DEFAULT '',
    tagline         TEXT NOT NULL DEFAULT '',
    stage           TEXT NOT NULL DEFAULT 'unset',    -- unset|idea|building|piloting|earning|paused|archived
    health          TEXT NOT NULL DEFAULT 'unknown',  -- green|yellow|red|unknown
    monthly_target  REAL NOT NULL DEFAULT 0,
    next_action     TEXT NOT NULL DEFAULT '',
    vault_path      TEXT NOT NULL DEFAULT '',
    sort_order      INTEGER NOT NULL DEFAULT 100,
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT INTO biz_divisions (id, name, emoji, tagline, vault_path, sort_order) VALUES
('consultations', 'Consultations & Automations', '🏢',
 'Automation consulting and custom builds for small businesses, trades and logistics.',
 'Your Business', 10),

('electronics', 'Electronics', '🖥️',
 'IT liquidation flipping and international electronics export.',
 'Northwind Electronics', 20),

('ecommerce', 'E-Commerce', '🛒',
 'Your own storefronts — eBay, Shopify, Marketplace.',
 '', 30);

-- Ventures gain a parent. NULL is meaningful and common: most of Blanco's
-- ventures (trading, studio, the cert track) are not Your Business work and stay
-- unparented. ON DELETE SET NULL so that removing a division orphans its
-- ventures rather than destroying their ledger history.
ALTER TABLE ventures ADD COLUMN division_id TEXT
    REFERENCES biz_divisions(id) ON DELETE SET NULL;

CREATE INDEX idx_ventures_division ON ventures(division_id);

-- The moves Blanco called on 2026-09-20. The rows are linked, never deleted:
-- each carries venture_events history, and dropping one would silently destroy
-- the ledger behind the numbers on the money tab.
UPDATE ventures SET division_id = 'electronics'   WHERE id IN ('northwind', 'electronics-export');
UPDATE ventures SET division_id = 'consultations' WHERE id = 'biz';

-- E-Commerce intentionally starts with no ventures. A storefront becomes one
-- when it exists, with division_id = 'ecommerce' — rather than seeding an
-- "eBay" row for a store that may not be open yet.

CREATE TABLE biz_deals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id  TEXT NOT NULL REFERENCES biz_divisions(id) ON DELETE CASCADE,
    client       TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    stage        TEXT NOT NULL DEFAULT 'lead',        -- lead|qualified|proposal|won|lost
    value        REAL NOT NULL DEFAULT 0,
    source       TEXT NOT NULL DEFAULT '',            -- example.com|referral|fiverr|...
    next_action  TEXT NOT NULL DEFAULT '',
    opened_on    TEXT NOT NULL,                       -- YYYY-MM-DD
    closed_on    TEXT,                                -- set when won or lost
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_biz_deals_division ON biz_deals(division_id, stage);
