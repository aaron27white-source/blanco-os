# 🛰️ Blanco OS

The engineering notes: every module, every design decision.

Personal agentic operating system. One local API that fuses the
second-brain vault, the OpenClaw agent fleet, the machine's own health, and the venture
ledger into a single typed surface — so a dashboard (and the agents themselves) can
answer *what is true right now, and what should I do next?*

The vault stays the source of truth. This is the nervous system on top of it.

## Run

It runs as a **systemd user service** — enabled at boot, restarts on failure:

```bash
systemctl --user status blanco-os
systemctl --user restart blanco-os
journalctl --user -u blanco-os -n 50
```

From Windows, `Desktop\todos.bat` makes sure the service is up, waits for
health, and opens the deck as a standalone Edge app window.

To run it in the foreground instead (development):

```bash
systemctl --user stop blanco-os
cd /home/you/workspace/blanco-os && ./run.sh
```

- **Command deck: <http://127.0.0.1:8800>**
- Interactive docs: <http://127.0.0.1:8800/docs>
- Spec: <http://127.0.0.1:8800/openapi.json> (also checked in at `docs/openapi.json`)
- Health: `curl -s localhost:8800/api/health`

First run creates and migrates `data/blanco_os.db` automatically.

## The one endpoint to know

```bash
curl -s localhost:8800/api/command/brief | jq
```

Focus, six stat tiles, a ranked NOW/NEXT list, open alerts, and live fleet status —
the entire home screen in one call. `GET /api/system` enumerates the nine modules so a
UI can build its own navigation.

## Modules

| Module | Prefix | Backed by | Writable |
|---|---|---|---|
| 🛰️ Command Deck | `/api/command` | everything, fused | focus, alerts |
| ✅ Tasks & Calendar | `/api/tasks`, `/api/events`, `/api/notes` | vault `02-areas/todo-data.json` | ✅ |
| 💰 Money & Ventures | `/api/money`, `/api/ventures` | OS database | ✅ |
| 🏦 Finance | `/api/finance` | OS database + vault `Debt-Tracker.md` | ✅ |
| 🎓 Cert Track | `/api/certs` | vault `Cert-Roadmap-Tracker.md` | overrides |
| 📓 Journal & Mood | `/api/journal` | vault `05-journal/diary.json` | ✅ |
| 💬 Messages | `/api/chat` | `openclaw agent` turns + OS notifications | ✅ |
| 📧 Inbox | `/api/email` | Gmail, via the categorizer's own rules | ✅ |
| ⏱️ Focus Timer | `/api/timer` | OS database | ✅ |
| 🗒️ Notepad | `/api/notepad` | OS database | ✅ |
| 🤖 Agent Fleet | `/api/agents` | roster + live probes | read-only |
| 🖥️ Systems | `/api/systems` | TCP / pgrep / stat / cron | read-only |
| 🧠 Knowledge | `/api/knowledge` | vault markdown scan | read-only |
| 🌉 Your Business Bridge | `/api/bridge` | OS database | ✅ |
| 📡 Live feed | `/api/stream` | SSE: `pulse` + `activity` | — |

The front end is a dependency-free `web/index.html`, served by the API itself
at `/`. No build step, no `node_modules`. It implements the "Blanco OS" design
from claude.ai/design against the live API. Its one companion page is
`web/notepad.html` — see the notepad note below.

## The notepad, and the big terminal

Two pieces of deck furniture that are worth knowing about:

- **Notepad** — the pill in the bottom-left corner (or `Ctrl`+`Shift`+`N` from
  anywhere) opens a floating window that drags, resizes, and remembers where it
  was. Its `⇱` button detaches it into a real browser window you can park on a
  second monitor while the deck carries on. Both are the *same page*,
  `web/notepad.html`, embedded in an iframe by the panel and opened directly by
  the popout — so the two can never drift apart. Notes autosave to
  `/api/notepad` about 700ms after you stop typing; every save carries the
  `updated_at` the editor last saw, so if the panel and the detached window are
  both on one note, the loser gets a 409 and a "changed in another window"
  banner instead of silently erasing the winner's text.
- **Terminal, big or small** — `⛶ expand` in the console's title bar (or
  `Ctrl`+`Shift`+`M`) lifts the terminal out of the deck to fill the screen and
  fades the HUD widgets around it; `Esc` or `⤡ shrink` puts it back. The choice
  is remembered, so a deck left maximised comes back maximised.

  The nav trigger stays clickable above the maximised terminal on purpose:
  `main` is a `z-index:2` stacking context, so a fixed card inside it can never
  paint above the chrome no matter how high its own `z-index` goes — the
  terminal starts below the trigger rather than fighting it.

## No invented data

The OS ships knowing only what is verifiable on this machine:

- **Agents are discovered**, not hardcoded — every directory under
  `workspace/subagents/` with an `IDENTITY.md` becomes an agent, with its name,
  emoji and role parsed from that file. Add a sub-agent and it appears.
- **Ventures carry identity only** — real name, emoji, and the vault/repo path
  they actually live at. Stage, monthly target, and next action all ship
  `unset`, because those are Blanco's calls, not the assistant's. The UI renders
  "not set" rather than a plausible-looking number.
- **Focus is read live** from the workspace `ACTIVE.md`, so it tracks whatever
  Sweet Jones has him focused on right now.
- Metrics with nothing to measure against report `health: "unknown"` and say so
  in their hint, instead of showing a red zero against a made-up goal.

## Agents write to it

Agents use `scripts/os` (symlinked to `~/.local/bin/os`) rather than hand-building
JSON. `os brief` prints the whole picture as text; `os alert`, `os task`, `os event`,
`os note` and `os bridge` write. See `workspace/BLANCO-OS-FOR-AGENTS.md`, which is
written for Sweet Jones and the sub-agents.

## Notifications

Two independent channels, both optional:

- **hq_inbox** — drops a markdown file into `workspace/hq/INBOX/`, which Sweet Jones
  already watches. No credential needed, and it routes through the agent that already
  owns Discord.
- **discord** — set `BLANCO_OS_DISCORD_WEBHOOK_URL` for a direct webhook. Worth having
  as well: it still works when OpenClaw is the thing that's down.

Anything `warning` or above is announced automatically on each sweep, deduped for 12
hours so a standing alert is announced once rather than every run. `POST
/api/command/notify/brief` pushes the morning brief (already on cron at 8 AM).

## The console

`/api/chat` is a conversation with one of **three commanders** and their sub-agents.

| engine | what answers | sub-agents are |
|---|---|---|
| `openclaw` | Sweet Jones, via the `openclaw agent` CLI | directories under `workspace/subagents/*/` with an `IDENTITY.md` |
| `claude` | a headless Claude Code turn in the workspace | markdown files under `.claude/agents/` (user scope, then project) |
| `hermes` | `hermes chat` in the gateway | Hermes profiles — genuinely isolated instances, picked with `HERMES_PROFILE` |

The set is closed on purpose. Adding a fourth is a real integration — how it takes a
prompt, how it names a conversation so it can be resumed, where its sub-agents are
declared — never a config string. But within each one, discovery is live: creating a
directory, a markdown file or a profile is the only step needed to see it in the deck.

Rosters are read from disk per request, since Blanco adds agents while the deck is open.
The one exception is `hermes profile list`, which costs a subprocess and is cached for 30s.

### Threads

A thread belongs to **one commander and one sub-agent**. Mixing them inside a thread
looked like one conversation while actually being three cold contexts wearing the same
scrollback, so switching commander now switches which threads exist.

Each thread remembers the commander's own conversation id, so reopening it resumes the
real thread rather than starting the CLI cold:

- **OpenClaw** takes `--session-key agent:<id>:deck-<n>`, its own threading primitive, so
  continuity needs no round-trip — the same key is the same conversation from turn one.
- **Claude Code** hands back a `session_id` in its first stream event; every turn after
  passes `--resume`. The id is kept even from a *failed* turn — a resumable thread is
  better than an orphaned one.
- **Hermes** announces its id via `chat --pass-session-id`, and prints it again in the
  footer it writes on exit, which is the fallback when the announcement is missing.

`PATCH /api/chat/sessions/{id}` with `{"forget": true}` drops the remembered id, starting
the agent fresh while keeping the transcript. Repointing a thread at another sub-agent
does the same implicitly: a resume handle belongs to the sub-agent that produced it, and
reusing one is how you get an agent answering with somebody else's context.

**Hermes needs `chat`, not the top-level `--oneshot`.** The latter is built for pipes and
starts from nothing every time — it *accepts* `--resume` and silently ignores the history,
which reads as an agent with amnesia rather than as an error. It also no longer runs with
`-Q`: quiet mode's own help says it suppresses "banner, spinner, **and tool previews**",
and those previews are the only sign of life Hermes gives during a turn. Oneshot behaviour
is implied on non-TTY stdio, so the flag bought nothing except hiding the working.

### Turns are never blocking

**A turn is a cold CLI start every time** — measured ~70s for OpenClaw even on a one-word
answer, because the binary boots an embedded runner per invocation. So `POST /api/chat`
returns `202` immediately with a `pending` assistant row and a background thread fills it
in. Nothing blocks.

Anything still `pending` at startup is closed out as failed: no daemon thread survives a
restart, so a pending row is waiting on a worker that no longer exists. One had been
"thinking" for three weeks.

Tests are guarded by an autouse fixture that fails loudly on any real commander
subprocess, covering **both** `run` (roster probes) and `Popen` (turns) — an earlier
version covered only `run`, and the first test to exercise the streaming worker quietly
reached the live CLI and passed on a real answer.

### Streaming

`GET /api/chat/{id}/stream` is an SSE feed for one turn — not just its answer, but its
**working**:

| event | meaning |
|---|---|
| `delta` / `replace` | the answer as it is written (`replace` for Hermes, which redraws its box rather than appending) |
| `activity` | tool calls, thinking, status. Shown above the answer, never stored as the reply |
| `usage` | tokens and dollars, once a commander reports them |
| `done` | exactly one, carrying the finished row |

This used to carry one string: the answer so far. That is all a reader needs *once the
answer starts* — and on an OpenClaw turn the answer started at second 55 of 59, so the
console was a spinner for the whole turn. Each commander is now driven so its working is
visible:

- **OpenClaw** goes over the **ACP bridge** (`openclaw acp`) rather than
  `openclaw agent --json`, which emits banners and then one JSON object at exit. Same
  Gateway, same session key, streaming protocol. See below.
- **Claude Code** already spoke `stream-json`; the deck was reading only `text_delta` out
  of it and discarding every tool call, tool result, thinking block and the `result`
  event's cost. All of it is surfaced now. `stdin` is also closed explicitly — Claude
  waited three seconds for input that was never coming, on every turn.
- **Hermes** runs without `-Q`, so its tool previews and timings come through.

Events are numbered from 1, so a browser that drops its connection reconnects with
`?after=` (or `Last-Event-ID`) and resumes rather than starting over. `GET
/api/chat/pending/all` says what a freshly loaded page should re-attach to — a reload
mid-turn used to strand the bubble as "pending" forever.

`POST /api/chat/{id}/cancel` stops a running turn and **keeps whatever it had written**.
Turns run in their own process group, so cancelling kills what the CLI spawned rather
than just the wrapper.

The live buffer (`turnstream.py`) is in memory, not SQLite: the row is written once, when
the turn finishes. A half-finished sentence is worth nothing after a restart, and a write
per token would cost more than the streaming saves. It outlives the turn by 120s so a
browser reconnecting just as the answer lands still gets its `done`.

### OS notices go to the alert tray

The timeline used to carry the OS's own notifications as `system` rows. It no longer
does. Everything `notify` announces started life as a row in the alert tray, which can
*acknowledge* a notice — in the timeline it only scrolled away between two questions.

### Why the local models are gone

The console used to offer every Ollama model on the box as its own tab. This machine has
**7.6 GB of RAM and 8 GB of VRAM** against a 6.3 GB Gemma and a 4.4 GB Mistral, so only
one model was ever resident: every switch paid an eviction plus a multi-GB reload, and
the answer came back with no tools, no vault and no memory. The three CLI commanders have
all of that.

### OpenClaw's latency, and what was actually done about it

The earlier reading here was that OpenClaw's ~70s turn was all thinking, that the gateway
on :8080 is an SPA rather than an API, and that the honest lever was therefore *perceived*
latency only. The first two are still true. The conclusion was not.

`openclaw acp` is a documented ACP bridge backed by the same Gateway: JSON-RPC over stdio,
with `session/update` notifications as the agent works, and it accepts the **same session
key** the deck was already using. Measured on this box, same prompt:

| path | time | behaviour |
|---|---|---|
| `openclaw agent --json` | **59s** | silent until exit |
| `openclaw acp` | **31s** | answer arrives in chunks |

So it was both real and perceived latency, roughly halved. Of the remaining 31s, ~23s is
the bridge's own `initialize` and only ~7s is the answer — **holding one bridge open per
thread would put an OpenClaw turn near shell speed.** That is deliberately not done yet:
a per-turn process cannot outlive its request, which is the safer default for a daemon
that runs unattended. `acp.py` is written so it stays a lifecycle change rather than a
protocol one.

`openclaw agent --json` is kept as a fallback if the bridge will not start, and is handed
the identical session key, so falling back does not quietly open a second empty
conversation beside the real one.

## Media control

`GET /api/nowplaying` reports every app on the PC that registers transport controls —
browser tabs included — and `POST /api/nowplaying/control` drives them.

This is Windows' `GlobalSystemMediaTransportControlsSessionManager`, reached over WSL
interop through `scripts/nowplaying.ps1`. A web page cannot inspect another browser tab
and certainly cannot talk to a Store app; Windows can do both, which is why one bridge
covers a YouTube tab in Opera, Spotify, and Media Player with no extension and no
per-app integration.

Every session is returned, not just the one in front, and every action takes an optional
`app_id` — so pausing the video in one browser leaves the music in another alone. Actions
are `play`, `pause`, `play_pause`, `next`, `previous`, `stop` and `seek`. `play` and
`pause` are separate from the toggle on purpose: a toggle fired from a strip that is a
second out of date does the opposite of what the button said.

Each session also reports what that app will actually accept (`can_seek`, `can_next`, …),
so the deck greys a control out rather than offering a button that silently does nothing.
Players with nothing loaded are dropped from the list — a browser keeps its session
registered long after the tab went quiet, and a picker full of blanks is worse than none.

Parameters are passed to PowerShell as bound parameters, never interpolated: an app id is
data from whatever happens to be running, and PowerShell would expand a `$(...)` inside
one. Everything degrades to `available: false` rather than raising, so the deck stays
usable on a box with no Windows underneath.

Album art is deliberately not read — see the comment in `nowplaying.ps1` for why (it
needs PowerShell 7, which is not installed here).

## Trends

`POST /api/command/history/snapshot` records the day's metrics (cron, 11:55 PM). Once
there are two days, every metric's `trend` and a `▲/▼ n vs last` hint fill in
automatically. Metrics where lower is better (open tasks) are flagged accordingly, so a
falling journal streak gets a ⚠ and falling open tasks does not.

## Backups

`scripts/backup-db.sh` — nightly `VACUUM INTO` + gzip, 14-day rotation, on cron at
11:30 PM. Uses SQLite's own snapshot rather than copying the file, so it is consistent
while the service is running.

## Write-through to the vault

Dashboard edits land in Blanco's real files, not a parallel copy:

| Action in the deck | What changes on disk |
|---|---|
| Add / edit / move a task | `02-areas/todo-data.json` (camelCase, the shape the old UI expects) |
| Add a calendar event | `todo-data.json` **and** the 📅 Appointments table in `todo-list.md`, rebuilt immediately |
| Quick note | `todo-data.json` |
| Journal entry | `05-journal/diary.json` (shared with The Scribe) |
| Record a debt payment, add or edit a debt | `blanco_os.db` **and** the 💸 What I Owe table in `Personal/areas/finance/Debt-Tracker.md`, rebuilt immediately |
| Cert progress, ventures, corrections | `blanco_os.db` only — see below |

`todo-list.md` is hand-curated by Blanco and Sweet Jones, so the sync **only** rebuilds
the Appointments section and refreshes the "Last updated" stamp. The Personal / Work /
Consultation tables are never touched. If the section marker is missing the sync reports
a 409 and leaves the file byte-identical rather than half-rewriting it. Pipes in titles
are escaped so a stray `|` can't break the table.

`Debt-Tracker.md` follows the same contract: only the **What I Owe** table and the
`**Updated:**` stamp are rewritten. The "What I'm Owed" table and the Log below it are
hand-curated and never touched, a missing section marker reports `ok: false` and leaves
the file byte-identical, and pipes in creditor names are escaped. The import runs the
other way exactly once — on boot, only when the `debts` table is empty — so a wiped
database comes back with what he actually owes instead of a blank tab.

Cert status and venture figures deliberately stay in the database: rewriting
`Cert-Roadmap-Tracker.md` would mean editing Blanco's own prose, so overrides layer on at
read time and flag themselves with `overridden: true`.

## Finance: advisory only

`/api/finance` tracks debts, investments, personal cash flow, and account notes, and it
computes a payoff plan. It **moves no money** — no payments, no trades, no broker or
Plaid connection, and no outbound call of any kind. A test asserts that by blocking
`socket.connect` and hitting the module.

The payoff planner returns **both** strategies with the interest each costs, then makes
one recommendation. Avalanche (highest rate first) minimises interest; snowball (smallest
balance first) minimises time to the first win. It recommends snowball unless avalanche
saves more than $100, because a plan nobody finishes saves nothing — and with no interest
rates recorded against the three sample debts, avalanche has nothing to optimise. Record
real rates and the answer can change on its own.

`/api/finance/notes` is where accounts opened, cards applied for, and terms worth
remembering get written down. It is **not** a credential store: `last4` is capped at four
digits by the schema, and no password, PIN, full account number, or security answer
belongs in an unencrypted SQLite file on a workstation. Note bodies are deliberately kept
out of the activity log so free text lives in exactly one place.

`/api/finance/log` is the module's slice of the shared append-only `activity_log` — every
payment, balance change, budget edit, note, and vault write, with a timestamp.

Personal money and business money stay apart on purpose. `/api/money` is the venture
ledger; `/api/finance` is Blanco's own balance sheet. Net worth counts only what he
personally holds — a venture's gross revenue is not a balance he can spend.

## Design decisions worth knowing

- **The database lives on the native fs, never `/mnt/c`.** SQLite journaling is
  unreliable over the vault's FUSE mount — the same bug that broke the it-parts-system
  CLI. The vault is read and written as plain files; only OS-owned state is SQLite.
- **Vault prose is parsed, never rewritten.** The cert roadmap stays hand-edited
  markdown; progress overrides layer on at read time and set `overridden: true` so the
  UI can show which is which.
- **Task and journal writes use the exact schemas the existing surfaces expect**
  (`todo-data.json` camelCase, `diary.json` date-keyed), so the Command Center HTML,
  the 4-hourly sync cron, and The Scribe all stay in agreement.
- **Reads degrade, they don't 500.** A missing or reshaped vault file yields empty
  results. The inbox reports `fetched: false` with a reason when Gmail is unreachable
  rather than erroring.
- **The inbox reuses `email_checker.py`** from the email-categorizer rather than
  reimplementing its VIP list, category rules and importance scoring — so the deck and
  the Discord `#emails` channel always tell the same story. Messages are never stored;
  they are fetched on demand and cached in memory for 90s.
- **The inbox learns.** When the categorizer scores something wrong, a correction is
  saved as a rule keyed by sender, domain, or category — never by message id, since
  messages are transient and the same offender comes back. Precedence is most-specific-
  wins: `sender` > `domain` > `category`, so demoting one LinkedIn newsletter does not
  demote LinkedIn job alerts. Every message carries both `agent_importance` and the
  final `importance`, plus `overridden_by`, so the UI can always show who decided what.
- **Timer state is server-side.** Reloading the deck cannot lose a countdown, a session
  that expired while the deck was closed auto-completes on the next read, and a partial
  unique index makes "only one session running" a database guarantee.
- **Notepad state is server-side too, and for the same reason.** localStorage would
  give the deck, the detached window and the phone three private copies that quietly
  disagree. `notepad_notes.updated_at` is the only timestamp in the database kept to
  millisecond resolution: it doubles as the concurrency token, and autosave fires every
  700ms — at `datetime('now')`'s one-second resolution two windows saving in the same
  second both look unchanged and the second one wins silently.
- **Nothing leaves the machine.** Every probe is a local TCP connect, `pgrep`, `stat`,
  or `crontab -l`. Bound to `127.0.0.1`.
- **The vault scan prunes `node_modules`/`.git`/`.venv` during the walk** and caches for
  30s. Without pruning, a single search took over 100 seconds on the FUSE mount; it is
  ~2.4s warm now.

## Tests

```bash
.venv/bin/python -m pytest -q      # 202 passed
```

Tests run against a throwaway vault and a temp database with outbound probes disabled —
the real second-brain is never touched.

## Configuration

Every path and probe is env-overridable with the `BLANCO_OS_` prefix — see
`.env.example`. Set `BLANCO_OS_PROBES_ENABLED=false` for offline/design work.

## Docs

- `docs/Blanco-OS-Outline.md` — the architecture outline: modules, screens, data-flow
  rules, build status, honest gaps
- `docs/Notepad-Widget-And-Terminal-Expand.md` — the notepad widget and the
  full-screen terminal: what was built, the two bugs the build turned up, what is
  still untested
- `docs/CLAUDE-DESKTOP-PROMPT.md` — paste-ready prompt for designing the frontend in
  Claude Desktop + 21st.dev
- `docs/openapi.json` — 40 paths, 48 schemas
