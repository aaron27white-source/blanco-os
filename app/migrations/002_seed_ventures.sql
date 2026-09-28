-- Demo ventures. Every name, number and path here is fictional; replace them
-- with your own through PATCH /api/ventures/{id}.

INSERT INTO ventures (id, name, emoji, thesis, stage, health, monthly_target, next_action, vault_path, repo_path, sort_order) VALUES
('northwind', 'Northwind Electronics', '🖥️',
 'Buy IT liquidation lots, strip and sell parts. Bid calculator + inventory + P&L already built.',
 'building', 'green', 1000,
 'Call the next five ITAD suppliers on the map',
 'Northwind Electronics', '', 10),

('biz', 'Your Business', '🏢',
 'Automation consulting and custom builds for small businesses.',
 'piloting', 'yellow', 2500,
 'Send three proposals this week',
 'Your Business', '', 20),

('import', 'Import Sourcing', '🌍',
 'Specialty goods sourced direct from producers for a local market.',
 'building', 'yellow', 1500,
 'Shortlist two suppliers and order samples',
 '01-projects/import-sourcing', '', 30),

('dispatch-bot', 'Dispatch Bot', '🦅',
 'Load posting bot — Discord-driven load flow.',
 'earning', 'green', 500,
 'Measure loads posted per week',
 '', '', 40),

('trading', 'Day Trading', '📈',
 'Self-directed trading with a documented system and alerts.',
 'piloting', 'yellow', 750,
 'Write down the entry/exit rules before sizing up',
 '01-projects/trading', '', 50),

('studio', 'Music & AV', '🎤',
 'Music catalog + AV production work as an income stream.',
 'building', 'unknown', 400,
 'Inventory the catalog and pick one release to push',
 '01-projects/studio', '', 60),

('career', 'AI Engineer Track', '🎓',
 'Certs -> remote AI engineering income.',
 'building', 'yellow', 3500,
 'Finish the next certification module',
 'Personal/projects/certs', '', 70);

INSERT INTO focus (id, headline, detail, horizon) VALUES
(1, 'Home server build',
 'Get the agent fleet running 24/7 independent of the laptop.',
 'week');
