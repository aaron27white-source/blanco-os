-- Command Deck console can now talk to either the OpenClaw agent CLI (Sweet
-- Jones and the sub-agents) or a headless Claude Code turn. `engine` records
-- which one produced/should produce each row so the UI can show the right
-- tab and the worker knows which binary to shell out to.

ALTER TABLE chat_messages ADD COLUMN engine TEXT NOT NULL DEFAULT 'openclaw';
