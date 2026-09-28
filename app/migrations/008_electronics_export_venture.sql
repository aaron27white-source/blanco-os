-- International electronics export — the broker-exploration venture documented
-- at 01-projects/electronics-export/ in the vault.
--
-- Following the rule migration 003 set: only what is verifiable on disk gets
-- seeded. Name, emoji and vault path are given by the operator; the monthly
-- target and next action are left unset for Blanco to fill in through
-- PATCH /api/ventures/electronics-export.
--
-- The handoff asked for stage 'exploring'. That is not one of the seven values
-- the Stage literal allows (unset|idea|building|piloting|earning|paused|
-- archived), so it cannot be stored. It also ships unset rather than being
-- mapped to the nearest value: test_ventures_carry_identity_but_no_invented
-- _numbers asserts no seeded venture arrives with a stage, and picking one on
-- Blanco's behalf is exactly the judgement call that test exists to prevent.
--
--     curl -X PATCH http://127.0.0.1:8800/api/ventures/electronics-export \
--          -H 'content-type: application/json' -d '{"stage":"idea"}'

INSERT INTO ventures (id, name, emoji, thesis, stage, health, monthly_target, next_action, vault_path, repo_path, sort_order) VALUES
('electronics-export', 'International Electronics Export', '📡',
 '',
 'unset', 'unknown', 0,
 '',
 '01-projects/electronics-export', '', 80);
