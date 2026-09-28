-- Credit module: scores, accounts, disputes, and the build-plan checklist.
--
-- Seeds are sample data. The disputes and the Week 0 -> Month 12 build plan
-- below are fictional (DEMO LENDER, EXAMPLE ST), so the page renders out of
-- the box. Following the rule migration 003 set, nothing that has to come from
-- a real credit report is guessed: no scores and no accounts are seeded.

-- Observed score readings. recorded_on is when the score was *seen* and is
-- distinct from created_at, the row's insert time: entering last week's
-- Credit Karma number should not date it today, or trend lines lie.
CREATE TABLE credit_scores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bureau      TEXT    NOT NULL,               -- Equifax | Experian | TransUnion
    score_type  TEXT    NOT NULL,               -- fico8 | auto | mortgage | bankcard | vantage3
    score       INTEGER NOT NULL,
    source      TEXT    NOT NULL DEFAULT 'manual',
    recorded_on TEXT    NOT NULL,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_credit_scores_lookup ON credit_scores (bureau, score_type, recorded_on DESC);

-- Tradelines, real and prospective.
--
-- `bucket` encodes the playbook's central rule: Bucket A is real credit
-- (Discover, Capital One, Self, Chime) and carries full weight with an
-- underwriter; Bucket B is fintech synthetic (Kikoff, Ava, Grow) which moves
-- FICO but reads as manufactured when it dominates a file. The playbook caps B
-- at 1-2 accounts, so the ratio has to be queryable, not just documented.
--
-- `monthly_cost` and `deposit` exist because the plan runs against a tight
-- monthly budget. A stack that quietly reaches $140/mo recurring is the failure
-- mode this column is here to make visible.
CREATE TABLE credit_accounts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    account_name   TEXT    NOT NULL,
    account_type   TEXT    NOT NULL,            -- secured_card | unsecured_card | installment |
                                                -- authorized_user | store_card | loan |
                                                -- rent_reporting | net30_vendor
    bucket         TEXT    NOT NULL DEFAULT 'A' CHECK (bucket IN ('A', 'B')),
    track          TEXT    NOT NULL DEFAULT 'personal' CHECK (track IN ('personal', 'business')),
    bureau         TEXT    NOT NULL DEFAULT 'all',
    credit_limit   REAL    NOT NULL DEFAULT 0,
    balance        REAL    NOT NULL DEFAULT 0,
    apr            REAL    NOT NULL DEFAULT 0,
    monthly_cost   REAL    NOT NULL DEFAULT 0,  -- recurring subscription/fee
    deposit        REAL    NOT NULL DEFAULT 0,  -- refundable security deposit
    opened_on      TEXT,
    closed_on      TEXT,
    payment_status TEXT    NOT NULL DEFAULT 'current',
    is_active      INTEGER NOT NULL DEFAULT 1,
    notes          TEXT    NOT NULL DEFAULT '',
    created_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Disputes. The handoff tracked scores and accounts but not this, which is the
-- only part of credit repair with a clock on it: the FCRA gives the bureau 30
-- days to respond, and a missed deadline is itself grounds for deletion. Without
-- response_due there is nothing to alert on.
CREATE TABLE credit_disputes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    bureau       TEXT    NOT NULL,
    category     TEXT    NOT NULL,              -- name | address | employer | tradeline | inquiry
    item         TEXT    NOT NULL,              -- the exact string as it appears on the report
    reason       TEXT    NOT NULL DEFAULT '',
    sent_on      TEXT,                          -- NULL until actually mailed/filed
    response_due TEXT,                          -- sent_on + 30 days, set by the service on send
    outcome      TEXT    NOT NULL DEFAULT 'pending'
                 CHECK (outcome IN ('pending', 'removed', 'verified', 'updated', 'no_response')),
    resolved_on  TEXT,
    notes        TEXT    NOT NULL DEFAULT '',
    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_credit_disputes_open ON credit_disputes (outcome, response_due);

-- The staged build plan, seeded from the playbook. Steps are rows rather than
-- a hardcoded state machine so Blanco can reorder, skip, or add without a
-- code change — the plan is his, the OS just tracks it.
CREATE TABLE credit_plan_steps (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    phase      TEXT    NOT NULL,                -- week0 | week1 | week2 | month3 | ...
    sort_order INTEGER NOT NULL,
    track      TEXT    NOT NULL DEFAULT 'personal'
               CHECK (track IN ('personal', 'business', 'repair')),
    title      TEXT    NOT NULL,
    detail     TEXT    NOT NULL DEFAULT '',
    est_cost   REAL    NOT NULL DEFAULT 0,
    done       INTEGER NOT NULL DEFAULT 0,
    done_on    TEXT,
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_credit_plan_order ON credit_plan_steps (done, sort_order);


-- ---------------------------------------------------------------------------
-- Seed: demo disputes for a fictional consumer (JANE DOE). Every name,
-- address and account below is invented.
-- ---------------------------------------------------------------------------
INSERT INTO credit_disputes (bureau, category, item, reason) VALUES
('Equifax', 'employer', 'ACME MOVERS LLC',
 'Listed as current employer with no verification date and no occupation. Never worked here. Employer data is not required to maintain the file and is a mixed-file vector.'),

('Experian', 'name', 'JANE DOA',
 'Listed under "Also known as". Misspelling of JANE DOE, not a legal name. An erroneous alias lets a furnisher claim an identity match on a debt that is not the consumer''s.'),

('Experian', 'address', '100 EXAMPLE ST, SPRINGFIELD, ST 00000',
 'Previous address, no longer current. Stale addresses create mixed-file risk and give furnishers weak-verification leverage.'),

('Experian', 'address', '200 SAMPLE AVE APT 1, SPRINGFIELD, ST 00000',
 'Previous address, no longer current. Same mixed-file and weak-verification risk.'),

('TransUnion', 'address', '100 EXAMPLE ST SPRINGFIELD, ST 00000',
 'Previous address. Only the current address is needed to maintain the file. This address is the linked identifier the DEMO LENDER tradeline uses to match the consumer, so it should be disputed in the same round.'),

('TransUnion', 'employer', 'ACME MOVERS',
 'Employer record with a typo in the occupation field. Never worked here and it is not current employment.'),

('TransUnion', 'tradeline', 'DEMO LENDER — charge-off, $90 balance',
 'Charged-off closed account still reporting a balance years after closure with no itemization; pay status contradicts the monthly ratings after the close date; no explicit Date of First Delinquency field (Metro 2), creating re-aging exposure.');


-- ---------------------------------------------------------------------------
-- Seed: sample build plan
-- ---------------------------------------------------------------------------
-- Week 0 clears any unpaid bank-closure record first: a new bank account pulls
-- ChexSystems, and an unpaid item is a decline at most banks.
INSERT INTO credit_plan_steps (phase, sort_order, track, title, detail, est_cost) VALUES
('week0', 10, 'repair', 'Pull the ChexSystems consumer disclosure',
 'Free from chexsystems.com. Confirms any unpaid DDA closure and which bank reported it. Do not pay anything before seeing the disclosure.', 0),
('week0', 20, 'repair', 'Settle the unpaid bank closure',
 'This gates the business bank account (Week 1) and Chime (Week 2). Ask the reporting bank in writing whether they will update ChexSystems to paid on settlement before sending money.', 100),
('week0', 30, 'repair', 'Confirm rent is verifiable before counting on Tier 0',
 'Rent backreporting buys up to 24 months of history and is the single biggest timeline compressor in the plan. It needs a landlord who will verify and a traceable payment record. If rent is cash or informal, Tier 0 collapses and the Month 6 projection moves out.', 0),

('week1', 100, 'repair', 'File all 7 disputes — one round, all three bureaus',
 'Six personal-information items plus the DEMO LENDER charge-off. Send the old-address disputes in the same round as the charge-off: those addresses are the identifiers the furnisher uses to match it to the consumer.', 0),
('week1', 110, 'personal', 'Rent backreport — Boom or RentReporters',
 'Pick one. Boom reports to all three bureaus, RentReporters to TU + EQ only. Ask specifically which bureaus receive the BACKDATED data, not just ongoing payments.', 95),
('week1', 120, 'personal', 'Experian Boost',
 'Free, Experian only. Covers the EX gap left by RentReporters. No reason to skip it.', 0),
('week1', 130, 'personal', 'Discover it Secured — open first',
 '$200 minimum deposit, no annual fee, all three bureaus, auto-review for graduation around month 7. This is the single most important account in the stack, and on a thin file it becomes the file''s age anchor. Never close it.', 200),
('week1', 140, 'personal', 'Capital One Platinum Secured',
 'Deposit as low as $49 for a $200 line, no annual fee, all three bureaus. Second real revolver so the file is not single-tradeline.', 49),
('week1', 150, 'business', 'Business foundation — EIN, bank account, D-U-N-S, 411',
 'D-U-N-S is free from D&B; do not pay to expedite. Use a real street address, not a PO box — that is the most common disqualifier. The bank account depends on Week 0 clearing.', 0),

('week2', 200, 'personal', 'Self Credit Builder',
 'Smallest plan around $25/mo, all three bureaus. Unlocks the Self Visa Secured after $100 is built — two tradelines from one product. Pick this OR Credit Strong, not both.', 25),
('week2', 210, 'personal', 'Kikoff',
 'About $5/mo for a ~$750 revolving line. Cheapest utilization lever available. Bucket B — counts against the 1-2 synthetic cap.', 5),
('week2', 220, 'personal', 'Grow Credit — free tier',
 'Free tradeline off existing subscriptions. Bucket B.', 0),
('week2', 230, 'personal', 'Chime Credit Builder',
 'Free, no interest, no credit check, underwritten on bank activity instead of FICO. Blocked until the ChexSystems item in Week 0 clears.', 0),
('week2', 240, 'business', 'Open 4-5 net-30 vendors',
 'Uline, Grainger, Quill, Summa Office Supplies, Crown Office Supplies. Use small, pay early.', 0),

('month1', 300, 'repair', 'Bureau responses due — 30 days from filing',
 'Anything with no response by the deadline is itself grounds for deletion. Verify removals on all three reports rather than trusting the response letter.', 0),

('month3', 400, 'personal', 'Stop opening accounts — inquiry cooldown begins',
 'No new applications from here until the funding round. Let the file age.', 0),
('month3', 410, 'business', 'Business Tier 2 — retail accounts',
 'Home Depot Pro, Lowe''s Commercial, Staples, Amazon Business.', 0),

('month5', 500, 'personal', 'Set up AZEO',
 'All cards report $0 except one showing 1-9% utilization. Worth 20-40 points on a thin file. Must be in place 30 days before any application.', 0),

('month6', 600, 'personal', 'First FICO generates',
 'FICO needs six months of history to produce a score at all. With the rent backreport the file should present roughly 30 months of history. This is the floor for having a score, not the funding date.', 0),
('month6', 610, 'business', 'Business Tier 3 — fleet cards',
 'WEX, Fuelman, Sunoco. Report to the business bureaus and line up directly with the freight path.', 0),

('month7', 700, 'personal', 'Discover graduation review',
 'Deposit is returned, the account stays open and keeps its age.', 0),

('month9', 800, 'personal', 'Real unsecured applications — personal and business',
 'Wants 680-700+ and a few aged revolvers. Realistic window is month 9-14, not month 6. Zero new inquiries for 60-90 days beforehand.', 0);
