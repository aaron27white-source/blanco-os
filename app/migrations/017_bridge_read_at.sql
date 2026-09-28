-- When a bridge notice was read, not just whether.
--
-- The Your Business inbox monitor learns from what Blanco does with its alerts, and
-- *how fast* he reads one is far more informative than the fact that he
-- eventually did: opening an alert within minutes says he wanted it, opening
-- it two days later says he tolerated it. Without this column the monitor has
-- to fall back to "now", which flattens that distinction and makes every read
-- look equally lukewarm.
--
-- NULL for every existing row, which is correct rather than backfilled: we do
-- not know when those were read, and inventing a timestamp would feed the
-- learning loop fabricated evidence — precisely the failure the whole design
-- is built to avoid.

ALTER TABLE bridge_messages ADD COLUMN read_at TEXT;
