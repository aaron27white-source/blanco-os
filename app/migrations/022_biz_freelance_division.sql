-- Freelance becomes Your Business's fifth division (Blanco, 2026-09-27): Upwork,
-- Fiverr and similar marketplace work, with the Proposals ✍️ Claude sub-agent
-- (~/.claude/agents/proposals.md) writing the bids.
--
-- Identity only, same rule as 016 and 018: no stage, target, health or next
-- action. Each job bid on is a biz_deals row with source = the platform.
-- No venture row until there's income to ledger against it.

INSERT INTO biz_divisions (id, name, emoji, tagline, vault_path, sort_order) VALUES
('freelance', 'Freelance', '✍️',
 'Upwork, Fiverr and marketplace jobs: bid, win, deliver.',
 '01-projects/freelance-launch', 50);
