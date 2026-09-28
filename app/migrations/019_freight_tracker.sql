-- The Freight Broker page: roadmap progress, a running log, and daily numbers.
--
-- The roadmap's steps themselves live in code (freight_service.STEPS), taken
-- from the playbook. Only Blanco's progress is stored: a row here means he
-- checked a step off. No row means not done. Nothing gets pre-checked.
CREATE TABLE freight_steps_done (
    step_id  TEXT PRIMARY KEY,
    done_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One row per thing he did: a call, a carrier vetted, a load booked.
CREATE TABLE freight_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    day         TEXT NOT NULL,                 -- YYYY-MM-DD, the day it happened
    kind        TEXT NOT NULL DEFAULT 'note',  -- call|carrier|shipper|load|money|paperwork|learning|win|note
    title       TEXT NOT NULL,
    detail      TEXT NOT NULL DEFAULT '',
    amount      REAL,                          -- NULL when the entry has no money in it
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_freight_log_day ON freight_log(day DESC, id DESC);

-- The daily dashboard feed: one row per day, upserted by hand or by whatever
-- dashboard Blanco plugs in. Margin is derived, never stored.
CREATE TABLE freight_daily (
    day                 TEXT PRIMARY KEY,
    calls               INTEGER NOT NULL DEFAULT 0,
    carriers_added      INTEGER NOT NULL DEFAULT 0,
    shippers_contacted  INTEGER NOT NULL DEFAULT 0,
    loads_booked        INTEGER NOT NULL DEFAULT 0,
    revenue             REAL NOT NULL DEFAULT 0,
    carrier_cost        REAL NOT NULL DEFAULT 0,
    notes               TEXT NOT NULL DEFAULT '',
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);
