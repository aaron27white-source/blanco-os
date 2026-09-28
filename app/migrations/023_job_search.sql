-- Job search: Personal → Job Search (Blanco, 2026-09-27).
--
-- Employment applications, kept apart from biz_deals on purpose: a deal is
-- Your Business selling work, an application is Blanco applying for a job. Nothing
-- is seeded — every row is one he adds.

CREATE TABLE job_applications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    company      TEXT NOT NULL,
    role         TEXT NOT NULL DEFAULT '',
    status       TEXT NOT NULL DEFAULT 'saved',   -- saved|applied|interviewing|offer|rejected|withdrawn
    source       TEXT NOT NULL DEFAULT '',        -- indeed|linkedin|referral|company site|...
    link         TEXT NOT NULL DEFAULT '',
    pay          TEXT NOT NULL DEFAULT '',        -- as posted, free text ("$22–26/hr")
    applied_on   TEXT,                            -- YYYY-MM-DD, set when status first reaches applied
    next_action  TEXT NOT NULL DEFAULT '',
    notes        TEXT NOT NULL DEFAULT '',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_job_applications_status ON job_applications(status);
