-- Freight Broker becomes Your Business's fourth division (Blanco, 2026-09-22).
--
-- Identity only, same rule as 016: no stage, target, health or next action.
-- The playbook's 12-month roadmap has revenue numbers in it, but those are a
-- plan, not a target Blanco has set — seeding one would put a guess on the
-- deck as though it were his number. He sets it through PATCH.
--
-- No venture row either: there is no brokerage yet (no LLC, no MC number),
-- and a venture that exists only to hold a $0 ledger is the invented-data
-- pattern migration 016 avoided for E-Commerce.

INSERT INTO biz_divisions (id, name, emoji, tagline, vault_path, sort_order) VALUES
('freight', 'Freight Broker', '🚚',
 'Dispatcher first, then licensed freight broker, then customs broker — one regional corridor.',
 'Your Business/projects/freight-broker', 40);
