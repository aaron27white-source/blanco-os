-- Migration 002 seeded ventures with targets, stages and "next actions" that
-- were authored by the assistant, not by Blanco. Those are removed here.
--
-- What survives is only what is verifiable on disk: the venture's name, its
-- emoji, and the vault/repo path it actually lives at. Every judgement call
-- (stage, health, what it should earn, what to do next) is now unset until
-- Blanco or an agent sets it through the API.

UPDATE ventures SET
    thesis         = '',
    stage          = 'unset',
    health         = 'unknown',
    monthly_target = 0,
    monthly_actual = 0,
    capital_in     = 0,
    next_action    = '',
    updated_at     = datetime('now');

-- Correct the one path that was wrong: import sourcing lives under
-- the Your Business compartment, not 01-projects.
UPDATE ventures
   SET vault_path = 'Your Business/projects/import-sourcing'
 WHERE id = 'import';

-- The seeded focus was a copy of ACTIVE.md frozen at build time. Removing the
-- row makes /api/command/focus fall back to reading ACTIVE.md live, so it
-- tracks whatever Sweet Jones has Blanco focused on right now.
DELETE FROM focus;
