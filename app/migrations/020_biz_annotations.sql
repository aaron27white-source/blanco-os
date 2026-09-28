-- Annotations: the register of who Blanco is signed up with, and what is
-- open with each of them.
--
-- This is deliberately NOT biz_deals. A deal is money in motion through a
-- pipeline and dies at won/lost; an annotation is a standing relationship —
-- Twilio, Render, Upwork, a client portal — that persists whether or not
-- there is a deal on. Mixing the two would bury two real proposals under
-- every SaaS signup confirmation in the mailbox.
--
-- The point of the row is the pair (who, what is open with them). A company
-- with nothing outstanding is still worth a row: knowing the account exists
-- is the whole reason Hermes sweeps the mail for it.

CREATE TABLE biz_annotations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Nullable on purpose, unlike biz_deals.division_id. Most signups
    -- (Twilio, Render, Cloudflare) are infrastructure under no single
    -- division. SET NULL rather than CASCADE: losing a division must not
    -- silently delete the record that an account exists.
    division_id   TEXT REFERENCES biz_divisions(id) ON DELETE SET NULL,

    company       TEXT NOT NULL,
    domain        TEXT NOT NULL DEFAULT '',        -- twilio.com — the stable identity
    kind          TEXT NOT NULL DEFAULT 'platform',-- platform|client|vendor|marketplace|service|other

    -- Which inbox the account is under. Blanco runs more than one address, and
    -- "which email did I sign up with" is half the reason to look a row up.
    account_email TEXT NOT NULL DEFAULT '',

    -- The two fields Blanco actually asked for: what he is in the middle of
    -- with them, and whether it is finished.
    project       TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'active',  -- active|needs_finish|blocked|done|dormant
    next_action   TEXT NOT NULL DEFAULT '',

    signed_up_on  TEXT,                            -- YYYY-MM-DD, NULL when unknown
    last_seen     TEXT,                            -- YYYY-MM-DD, newest mail from them
    source        TEXT NOT NULL DEFAULT '',        -- gmail thread id or link Hermes pulled it from
    notes         TEXT NOT NULL DEFAULT '',

    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Hermes re-runs the mail sweep on demand, so the natural key has to stop the
-- second run from doubling every row. (company, account_email) rather than
-- company alone: the same vendor under two addresses is two real accounts.
-- The service upserts on this constraint and never clobbers a field Blanco
-- typed with an empty one the sweep inferred.
CREATE UNIQUE INDEX idx_biz_annotations_identity
    ON biz_annotations(company, account_email);

CREATE INDEX idx_biz_annotations_status ON biz_annotations(status, company);
CREATE INDEX idx_biz_annotations_division ON biz_annotations(division_id);
