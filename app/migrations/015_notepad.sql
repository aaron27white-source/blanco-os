-- Notepad: the scratch surface the popout widget writes into.
--
-- Deliberately not the vault's `notes` array in todo-data.json. Those are
-- one-line quick notes the scheduler owns; this is free-form text Blanco
-- types mid-thought, from any view and from a detached window, and it has to
-- survive a reload, a second window, and the phone — so it lives in the OS
-- database and not in localStorage.

CREATE TABLE notepad_notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    title      TEXT NOT NULL DEFAULT '',
    body       TEXT NOT NULL DEFAULT '',
    pinned     INTEGER NOT NULL DEFAULT 0,          -- pinned notes sort first
    color      TEXT NOT NULL DEFAULT 'default',     -- default|red|amber|green|blue|violet
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    -- Millisecond resolution, unlike every other timestamp in this database.
    -- `updated_at` is the concurrency token the two editors compare against,
    -- and autosave fires every 700ms — at datetime('now')'s one-second
    -- resolution two windows saving in the same second both look unchanged
    -- and the second one silently wins.
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f', 'now'))
);

-- The list view is "pinned first, then most recently touched", and it is read
-- on every keystroke-debounced save from up to two windows at once.
CREATE INDEX idx_notepad_order ON notepad_notes(pinned DESC, updated_at DESC);

INSERT INTO notepad_notes (title, body) VALUES
  ('Scratch', 'Anything half-formed goes here. Ctrl+Shift+N opens this from any view.');
