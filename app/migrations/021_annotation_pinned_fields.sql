-- Remember which annotation fields Blanco edited by hand.
--
-- Migration 020 protected his edits with a heuristic: the sweep may fill a
-- field that is blank, never one that already has a value. That is wrong at
-- exactly the moment it matters most — *clearing* a field is an edit too.
-- Hermes' first real sweep pulled the word "available" out of an Appen mail
-- as a project name; clearing it did nothing, because the next sweep saw an
-- empty field and filled it straight back in. The endpoint's own docs
-- promised that could not happen.
--
-- A blank cannot carry that intent on its own, so it is recorded beside the
-- row instead: every field a PATCH writes is pinned, and a pinned field is
-- untouchable by any later sweep whatever its value. Import keeps filling
-- genuinely untouched fields, so a sweep that finally learns a project still
-- gets to record it.
--
-- Comma-separated rather than a join table: the list is at most a dozen short
-- column names, only ever read as a whole, and never queried across rows.
ALTER TABLE biz_annotations
    ADD COLUMN pinned_fields TEXT NOT NULL DEFAULT '';
