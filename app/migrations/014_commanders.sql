-- Three commanders, no local models.
--
-- The Ollama tabs are gone. This box has 7.6 GB of RAM and 8 GB of VRAM
-- against a 6.3 GB Gemma and a 4.4 GB Mistral, so only one model was ever
-- resident and every switch paid an eviction plus a multi-GB reload — for an
-- answer with no tools, no vault and no memory. The three CLI commanders
-- already installed in the gateway have all of that, so the console is now
-- OpenClaw, Claude Code and Hermes, each with its own sub-agents.
UPDATE chat_sessions SET engine = 'openclaw' WHERE engine LIKE 'ollama:%';
UPDATE chat_messages SET engine = 'openclaw' WHERE engine LIKE 'ollama:%';

-- A session now belongs to exactly one commander and one of its sub-agents.
-- Mixing engines inside a thread read as one conversation but was three
-- separate cold contexts wearing the same scrollback.
ALTER TABLE chat_sessions ADD COLUMN agent TEXT NOT NULL DEFAULT 'main';

-- The id the commander itself gave the underlying conversation, so reopening
-- a session resumes the real thread instead of starting the CLI cold:
-- `claude --resume`, `hermes --resume`, `openclaw --session-key`.
ALTER TABLE chat_sessions ADD COLUMN native_session_id TEXT NOT NULL DEFAULT '';

-- System notices moved out of the timeline and into the alert tray, where
-- they can be acknowledged instead of scrolling away between two questions.
DELETE FROM chat_messages WHERE role = 'system';
