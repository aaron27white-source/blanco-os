-- Finance module: debts, investments, and personal cash flow.
--
-- Scope boundary against the money module (migration 001/002): `ventures` and
-- `venture_events` are the *business* ledger — what a stream earned and spent.
-- Everything here is Blanco's *personal* money. They stay separate because
-- "the shop grossed $400" and "I paid a lender $50" answer different
-- questions and folding them together makes both attainment and net worth wrong.
--
-- Following the rule migration 003 set: only what is verifiable on disk gets
-- seeded. The three debts below are seeded because they are recorded in the
-- vault at Personal/areas/finance/Debt-Tracker.md. No investment accounts,
-- goals, transactions, or budgets are seeded — none exist on disk yet.
--
-- This module is advisory. Nothing here moves real money.

-- What Blanco owes. `amount` is the principal as first recorded and never
-- changes; `balance` is what is left. Keeping both is what makes "you've paid
-- off 60% of a debt" answerable — a single decrementing column loses the
-- denominator the moment the first payment lands.
CREATE TABLE debts (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor      TEXT    NOT NULL,
    amount        REAL    NOT NULL,
    balance       REAL    NOT NULL,
    -- Annual percentage rate. NULL, not 0, when it is genuinely unknown:
    -- avalanche ordering has to be able to tell "no interest" from "not asked",
    -- and a demo loan at 0% is a real answer while an unset rate is not.
    interest_rate REAL,
    due_date      TEXT,
    status        TEXT    NOT NULL DEFAULT 'outstanding'
                  CHECK (status IN ('outstanding', 'partial', 'paid')),
    notes         TEXT    NOT NULL DEFAULT '',
    added_at      TEXT    NOT NULL DEFAULT (date('now')),
    updated_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_debts_status ON debts (status, balance);

-- Every payment against a debt, kept as rows rather than only as activity_log
-- entries. The log is an append-only audit trail meant to be read; this is the
-- table that has to be *summed* to rebuild a balance, and reconstructing that
-- from JSON blobs in a text column would be a query nobody wants to write.
CREATE TABLE debt_payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    debt_id    INTEGER NOT NULL REFERENCES debts (id) ON DELETE CASCADE,
    amount     REAL    NOT NULL,
    paid_on    TEXT    NOT NULL DEFAULT (date('now')),
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_debt_payments_debt ON debt_payments (debt_id, paid_on);

-- Investment accounts. Balances are entered by hand: v1 fetches no market data
-- and holds no broker credentials, so `updated_at` is the honest age of the
-- number and the agent is expected to quote it alongside the balance.
CREATE TABLE investment_accounts (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    name                 TEXT    NOT NULL,
    type                 TEXT    NOT NULL DEFAULT 'brokerage'
                         CHECK (type IN ('brokerage', 'retirement', 'crypto', 'cash')),
    balance              REAL    NOT NULL DEFAULT 0,
    contributions_to_date REAL   NOT NULL DEFAULT 0,
    notes                TEXT    NOT NULL DEFAULT '',
    updated_at           TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at           TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE investment_goals (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT    NOT NULL,
    target_amount       REAL    NOT NULL,
    monthly_contribution REAL   NOT NULL DEFAULT 0,
    target_date         TEXT,
    current_value       REAL    NOT NULL DEFAULT 0,
    notes               TEXT    NOT NULL DEFAULT '',
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at          TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Personal cash flow. `kind` is stored rather than inferred from the sign so
-- that amounts are always positive and a mistyped minus cannot silently turn
-- income into an expense.
CREATE TABLE transactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    occurred_on TEXT   NOT NULL DEFAULT (date('now')),
    category   TEXT    NOT NULL DEFAULT 'uncategorized',
    amount     REAL    NOT NULL CHECK (amount >= 0),
    kind       TEXT    NOT NULL CHECK (kind IN ('income', 'expense', 'debt_payment')),
    note       TEXT    NOT NULL DEFAULT '',
    created_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_transactions_month ON transactions (occurred_on, kind);

-- Monthly budget per category. One row per category, not per category-month:
-- a budget is a standing intention that gets revised, and versioning it by
-- month would ask Blanco to re-enter the same twelve numbers every January.
CREATE TABLE budget_categories (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT    NOT NULL UNIQUE,
    monthly_budget REAL    NOT NULL DEFAULT 0,
    notes          TEXT    NOT NULL DEFAULT '',
    updated_at     TEXT    NOT NULL DEFAULT (datetime('now')),
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);

-- Free-form notes: accounts opened, cards applied for, who to call, what the
-- terms were. Deliberately loose, because the point is that Blanco writes it
-- down at all rather than fitting it to a schema.
--
-- NOT a credential store. No passwords, PINs, full account numbers, or
-- security answers belong in here: this file is an unencrypted SQLite database
-- on a workstation, backed up in the clear, and readable by anything that can
-- read the disk. `last4` exists so a card is *identifiable* without the number
-- that would make it usable.
CREATE TABLE finance_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT    NOT NULL,
    category   TEXT    NOT NULL DEFAULT 'general'
               CHECK (category IN ('account', 'card', 'loan', 'subscription',
                                   'contact', 'tax', 'general')),
    body       TEXT    NOT NULL DEFAULT '',
    institution TEXT   NOT NULL DEFAULT '',
    last4      TEXT    NOT NULL DEFAULT '',
    opened_on  TEXT,
    -- Pinned notes are the handful he needs at the top permanently — the card
    -- he is building history on, the account the rent comes out of.
    pinned     INTEGER NOT NULL DEFAULT 0,
    archived   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_finance_notes_live ON finance_notes (archived, pinned DESC, id DESC);

-- Seeded from the vault ledger at Personal/areas/finance/Debt-Tracker.md as of
-- 2026-08-02. Interest rates are left NULL because the ledger records none.
INSERT INTO debts (creditor, amount, balance, status, notes, added_at) VALUES
('Cedar Bank',  5000.00, 5000.00, 'outstanding', 'demo data', '2026-08-02'),
('Birch Loans',  350.00,  350.00, 'outstanding', '',          '2026-08-02'),
('Pine Credit', 2500.00, 2500.00, 'outstanding', '',          '2026-08-02');
