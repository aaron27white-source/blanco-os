"""The three commanders and how to reach each one.

A "commander" is a CLI installed in the gateway that can hold a conversation:
OpenClaw, Claude Code, Hermes. They are not interchangeable back-ends behind
one adapter — each has its own sub-agent format, its own way of naming a
conversation so it can be resumed, and its own flags. This module keeps those
three sets of facts in one place so the chat service can stay a thin caller.

Three things are modelled per commander:

* **Roster** — who you can address. OpenClaw sub-agents are directories with an
  `IDENTITY.md`, Claude's are markdown files with YAML frontmatter, Hermes' are
  isolated profiles. Nothing is invented: a roster is whatever is actually on
  disk, so creating a sub-agent is the only step needed to see it in the deck.
* **Argv** — the full native invocation, including the model override and the
  resume handle, so a turn from the deck is the same turn you would get from a
  terminal rather than a stripped-down one.
* **Reply parsing** — pulling the answer *and* the conversation id back out, so
  the next turn in that session continues instead of starting cold.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from app.config import get_settings

# Sub-agent rosters are read from disk on every request. They are a handful of
# small files and Blanco adds agents while the deck is open — caching would
# only buy microseconds and cost him a restart to see his own new agent.

HEADING_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)
ROLE_RE = re.compile(r"\*\*Role:\*\*\s*(.+)")
FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
# Emoji live outside the BMP or in the symbol blocks; good enough to split a
# heading like "Circuit 💻 — Northwind Electronics Sub-Agent".
EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿️]+")

# `hermes profile list` is a table render, not JSON. One row per profile, the
# id optionally prefixed with a "current profile" diamond.
HERMES_PROFILE_RE = re.compile(r"^\s*[◆◇*]?\s*([A-Za-z0-9][\w.-]*)\s{2,}(\S.*)?$")
# `hermes sessions list` prints ids as 20260829_233643_8844b3.
HERMES_SESSION_ID_RE = re.compile(r"\b(\d{8}_\d{6}_[0-9a-f]{6})\b")
# `chat -Q --pass-session-id` announces the session on its own line before the
# answer. Everything after that line is the reply.
HERMES_SESSION_LINE_RE = re.compile(r"^session_id:\s*(\S+)\s*$", re.MULTILINE)
# Status lines Hermes prints before the answer even in quiet mode — a resume
# notice and a YOLO-restored notice, each led by a glyph.
HERMES_STATUS_RE = re.compile(r"^\s*[↻⚡✓✗⚠·]")
# Matched against the *extracted answer*, so it only catches a failure Hermes
# printed where the reply belongs. A failure printed outside the answer box —
# a missing credential, say — never reaches this; see FATAL_OUTPUT_RES below,
# which is checked against the raw stream for exactly that reason.
HTTP_ERROR_RE = re.compile(r"^HTTP \d{3}:")

# Hermes is no longer run with -Q. Its own help says quiet mode suppresses
# "banner, spinner, and tool previews" — which is precisely the working a
# terminal shows while an agent is busy, and without it the deck had nothing
# to display for the length of a turn. Non-TTY stdio already implies oneshot,
# so dropping the flag costs no behaviour, it only stops throwing the progress
# away. What it does cost is parsing, hence the four patterns below.
ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07")
# The answer arrives inside a box: "╭─ ⚕ Hermes ───╮", the text, then "╰───╯".
HERMES_BOX_OPEN_RE = re.compile(r"^\s*╭─.*(?:Hermes|⚕|⚡)")
HERMES_BOX_CLOSE_RE = re.compile(r"^\s*╰─+")
# Tool previews: "  ┊ ⚡ preparing mcp__memory…" / "  ┊ 🧠 memory  +user: …  0.0s"
HERMES_TOOL_RE = re.compile(r"^\s*┊\s*(.+?)\s*$")
# The footer carries the session id even when --pass-session-id printed nothing.
HERMES_FOOTER_SESSION_RE = re.compile(r"^Session:\s*(\S+)\s*$", re.MULTILINE)

# Failures a commander prints as ordinary output before exiting 0.
#
# A non-zero exit is already handled; these are the ones that are not. Hermes
# with no credential for its configured provider says so in one line, says
# "Goodbye!" and exits successfully — so the deck stored the apology as the
# answer and called the turn `done`. A turn that never reached a model is a
# failed turn, and it has to say so, or the tab looks like an agent that
# replies with an error message rather than one that cannot start.
FATAL_OUTPUT_RES: tuple[re.Pattern[str], ...] = (
    re.compile(r"No usable credentials found for provider '([^']+)'", re.I),
    re.compile(r"Set ([A-Z0-9_]+_API_KEY)\b"),
    re.compile(r"\bmodel '([^']+)' (?:not found|is not available)", re.I),
)


def fatal_output(text: str) -> str:
    """The failure a commander printed instead of raising, or ''.

    Matched against the whole turn rather than the extracted answer: Hermes
    prints this outside its answer box, so the box itself is empty and the
    reason would otherwise be dropped on the floor.
    """
    for pattern in FATAL_OUTPUT_RES:
        if match := pattern.search(text):
            return match.group(0).strip()
    return ""


CLAUDE_MODEL_ALIASES = ("opus", "sonnet", "haiku", "fable")
MODEL_DIRECTIVE_RE = re.compile(r"^\s*@(" + "|".join(CLAUDE_MODEL_ALIASES) + r")\b[:\s]*", re.I)


def model_directive(message: str) -> tuple[str, str]:
    """('opus', rest) for a message that opens with "@opus", else ('', message).

    Only a leading directive counts, and only the CLI's own aliases, so an
    email address or an "@someone" mid-sentence is never mistaken for one.
    """
    match = MODEL_DIRECTIVE_RE.match(message)
    if not match:
        return "", message
    rest = message[match.end():]
    if not rest.strip():            # a bare "@sonnet" is a message, not a directive
        return "", message
    return match.group(1).lower(), rest


def strip_ansi(text: str) -> str:
    """Plain text out of a stream written for a terminal.

    Progress lines are redrawn with carriage returns rather than newlines, so
    only the last segment of a \r-joined line is the current state — keeping
    every segment would show each frame of a spinner as its own event.
    """
    return ANSI_RE.sub("", text)


def visible_line(line: str) -> str:
    """The state a line ends up in, after any in-line redraws.

    The trailing newline has to come off *before* splitting on carriage
    returns: a CRLF line ending is not a redraw, and treating it as one leaves
    every progress line empty — which is exactly how the tool previews went
    missing the first time this was written.
    """
    text = strip_ansi(line).rstrip("\n").rstrip("\r")
    segments = [seg for seg in text.split("\r") if seg.strip()]
    return (segments[-1] if segments else text.split("\r")[-1]).rstrip()


# Hermes closes a run with a block written for a human sitting at a prompt.
# None of it is the reply, and echoing it as progress just adds noise.
HERMES_FOOTER_PREFIXES = (
    "Resume this session with", "hermes --resume", "hermes -c ",
    "Session:", "Title:", "Duration:", "Messages:", "Query:",
)
PROFILE_LIST_TIMEOUT = 10.0
PROFILE_CACHE_SECONDS = 30.0
_HERMES_PROFILE_CACHE: tuple[float, str] | None = None


@dataclass(frozen=True)
class Commander:
    """Everything the deck needs to know about one CLI."""

    id: str
    name: str
    emoji: str
    note: str
    #: The sub-agent flag's value when talking to the commander itself.
    primary_id: str
    primary_name: str
    primary_emoji: str
    candidates: tuple[str, ...] = field(default=())

    def binary(self) -> str:
        """First candidate that exists, or the last one so the error names it.

        systemd gives Blanco OS a minimal PATH, so an absolute path has to come
        first — a bare name resolves in his terminal and then fails under the
        service, which is a genuinely confusing way to lose a feature.
        """
        for candidate in self.candidates:
            if "/" in candidate:
                if Path(candidate).is_file():
                    return candidate
            elif (found := shutil.which(candidate)):
                return found
        return self.candidates[-1] if self.candidates else self.id

    def available(self) -> bool:
        return Path(self.binary()).is_file()


OPENCLAW = Commander(
    id="openclaw",
    name="OpenClaw",
    emoji="🍯",
    note="Sweet Jones and her domain sub-agents — the vault, the ventures, the routing",
    primary_id="main",
    primary_name="Sweet Jones",
    primary_emoji="🍯",
    candidates=("/usr/local/bin/openclaw", "openclaw"),
)

CLAUDE = Commander(
    id="claude",
    name="Claude Code",
    emoji="🦊",
    note="headless build turns from the workspace root — git, scripts, full-stack work",
    primary_id="default",
    primary_name="Claude Code",
    primary_emoji="🦊",
    candidates=("/home/you/.local/bin/claude", "claude"),
)

HERMES = Commander(
    id="hermes",
    name="Hermes",
    emoji="⚡",
    note="the gateway agent — tools, skills and memory, one isolated profile per sub-agent",
    primary_id="default",
    primary_name="Hermes",
    primary_emoji="⚡",
    candidates=("/home/you/.local/bin/hermes", "hermes"),
)

ALL: tuple[Commander, ...] = (OPENCLAW, CLAUDE, HERMES)
BY_ID = {c.id: c for c in ALL}
DEFAULT_ENGINE = OPENCLAW.id


def get(engine: str) -> Commander:
    try:
        return BY_ID[engine]
    except KeyError:
        raise LookupError(f"no commander named {engine!r}") from None


# --------------------------------------------------------------------------
# rosters
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Subagent:
    id: str
    engine: str
    name: str
    emoji: str
    role: str
    source: str
    primary: bool = False


def _primary(commander: Commander, source: str) -> Subagent:
    return Subagent(
        id=commander.primary_id,
        engine=commander.id,
        name=commander.primary_name,
        emoji=commander.primary_emoji,
        role=commander.note,
        source=source,
        primary=True,
    )


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_identity(text: str) -> tuple[str, str, str]:
    """(name, emoji, role) from an OpenClaw sub-agent's IDENTITY.md."""
    heading_match = HEADING_RE.search(text)
    heading = heading_match.group(1).strip() if heading_match else ""

    # "Circuit 💻 — Northwind Electronics Sub-Agent" -> left half is name + emoji.
    left = re.split(r"\s+[—–-]\s+", heading, maxsplit=1)[0].strip()
    emojis = EMOJI_RE.findall(left)
    emoji = emojis[0].strip() if emojis else "🤖"
    name = EMOJI_RE.sub("", left).strip() or "Sub-agent"

    role_match = ROLE_RE.search(text)
    role = role_match.group(1).strip().rstrip(".") if role_match else ""
    # Drop the trailing example list, keep the headline responsibility.
    role = re.split(r"\s+[—–]\s+", role, maxsplit=1)[0].strip() if role else "Sub-agent"
    return name, emoji, role


def _openclaw_registered() -> list[dict]:
    """`agents.list` out of openclaw.json — the ids OpenClaw itself answers to.

    Reading only `workspace/subagents/*/IDENTITY.md` listed the folders Blanco
    writes his identity docs into, which is not the same set: agents he had
    registered and renamed (Circuit, Key, Score, Docket…) never appeared, and
    the folder names that did appear are not what `--agent` accepts.
    """
    try:
        raw = json.loads(get_settings().openclaw_config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = ((raw.get("agents") or {}).get("list")) if isinstance(raw, dict) else None
    return [e for e in entries if isinstance(e, dict) and e.get("id")] if isinstance(entries, list) else []


def _identity_docs() -> dict[str, tuple[str, str, str, Path]]:
    """Every IDENTITY.md under the subagents folder, keyed by folder name."""
    root = get_settings().openclaw_subagents_dir
    docs: dict[str, tuple[str, str, str, Path]] = {}
    if not root.is_dir():
        return docs
    for child in sorted(root.iterdir()):
        identity = child / "IDENTITY.md"
        if child.is_dir() and identity.is_file():
            name, emoji, role = parse_identity(_read(identity))
            docs[child.name] = (name, emoji, role, identity)
    return docs


def _openclaw_roster() -> list[Subagent]:
    settings = get_settings()
    root = settings.openclaw_subagents_dir
    out = [_primary(OPENCLAW, str(root.parent))]
    docs = _identity_docs()
    used: set[str] = set()

    for entry in _openclaw_registered():
        agent_id = str(entry["id"])
        if agent_id in {OPENCLAW.primary_id, "default"} or entry.get("default"):
            continue
        identity = entry.get("identity") or {}
        name = str(entry.get("name") or identity.get("name") or agent_id.title())
        emoji = str(identity.get("emoji") or "🤖")

        # The role comes from an IDENTITY.md if one can be found for this agent:
        # its own configured workspace first, then the folder of the same name,
        # then a folder whose doc carries the same emoji — which is how the
        # renamed agents (Circuit was Electronics, Mic was Music) still get
        # their written role instead of showing up blank.
        workspace = str(entry.get("workspace") or "")
        role, source = "", workspace or str(settings.openclaw_config_file)
        own = Path(workspace) / "IDENTITY.md" if workspace else None
        if own and own.is_file():
            _, _, role = parse_identity(_read(own))
            source = str(own)
            # Claim the folder, or the leftovers pass below lists this agent a
            # second time under its folder name.
            used.add(own.parent.name)
        else:
            key = next(
                (k for k in (agent_id, Path(workspace.rstrip("/")).name) if k in docs and k not in used),
                None,
            ) or next((k for k, v in docs.items() if v[1] == emoji and k not in used), None)
            if key:
                role = docs[key][2]
                source = str(docs[key][3])
                used.add(key)
        model = str(entry.get("model") or "")
        out.append(Subagent(
            agent_id, OPENCLAW.id, name, emoji,
            role or f"registered in openclaw.json{' · ' + model if model else ''} — no IDENTITY.md yet",
            source,
        ))

    # Identity docs with no registered agent behind them: still shown, because
    # they are real work Blanco wrote, but they are the folder name, not an id
    # OpenClaw would accept.
    for key, (name, emoji, role, identity) in docs.items():
        if key in used:
            continue
        out.append(Subagent(key, OPENCLAW.id, name, emoji, role, str(identity)))
    return out


def _frontmatter_field(block: str, key: str) -> str:
    """One scalar out of a YAML frontmatter block, without a YAML dependency.

    Claude's agent frontmatter is flat `key: value` pairs; anything richer than
    that belongs to the agent's own config, not to a roster listing.
    """
    match = re.search(rf"^{key}\s*:\s*(.+)$", block, re.MULTILINE)
    return match.group(1).strip().strip("'\"") if match else ""


CLAUDE_AGENT_BADGE_RE = re.compile(r"^[\w &'-]{1,40}?\s([^\w\s—–-]{1,4})\s+[—–-]\s")


def _claude_roster() -> list[Subagent]:
    dirs = get_settings().claude_agents_dirs
    out = [_primary(CLAUDE, str(get_settings().workspace_path))]
    seen = {CLAUDE.primary_id}
    for root in dirs:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.md")):
            block = FRONTMATTER_RE.match(_read(path))
            meta = block.group(1) if block else ""
            agent_id = _frontmatter_field(meta, "name") or path.stem
            if agent_id in seen:
                # User scope is read first and wins: a project file with the
                # same name is the same agent, not a second one.
                continue
            seen.add(agent_id)
            description = _frontmatter_field(meta, "description")
            # Descriptions Blanco's agents are written with open "Name 💼 — …";
            # that emoji is the agent's own, so the tab wears it. 🦊 otherwise.
            badge = CLAUDE_AGENT_BADGE_RE.match(description)
            out.append(
                Subagent(
                    id=agent_id,
                    engine=CLAUDE.id,
                    name=agent_id.replace("-", " ").title(),
                    emoji=badge.group(1) if badge else "🦊",
                    # Descriptions are written for the router and run long;
                    # the first sentence is the part a human tab needs.
                    role=re.split(r"(?<=[.!?])\s", description, maxsplit=1)[0][:120],
                    source=str(path),
                )
            )
    return out


def _hermes_profile_list() -> str:
    """`hermes profile list` stdout, cached briefly.

    This is the only roster source that costs a subprocess, and the roster is
    read by the agents view, the console tabs and the alert sweep — so without
    a cache an unrelated endpoint pays half a second to learn something that
    changes when Blanco creates a profile, which is rarely.
    """
    global _HERMES_PROFILE_CACHE
    if not get_settings().probes_enabled or not HERMES.available():
        return ""
    if _HERMES_PROFILE_CACHE:
        cached_at, stdout = _HERMES_PROFILE_CACHE
        if time.monotonic() - cached_at < PROFILE_CACHE_SECONDS:
            return stdout
    try:
        proc = subprocess.run(
            [HERMES.binary(), "profile", "list"],
            capture_output=True, text=True, timeout=PROFILE_LIST_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    _HERMES_PROFILE_CACHE = (time.monotonic(), proc.stdout or "")
    return _HERMES_PROFILE_CACHE[1]


def _hermes_roster() -> list[Subagent]:
    """Hermes sub-agents are its profiles — genuinely isolated instances, each
    with its own config, memory and session store, selected with `-p <name>`.

    `hermes profile list` renders a table rather than JSON, so this parses the
    rows. A parse that finds nothing degrades to the default profile alone,
    which is exactly what a box with no extra profiles should show anyway.
    """
    out = [_primary(HERMES, str(Path.home() / ".hermes"))]
    stdout = _hermes_profile_list()
    for line in stdout.splitlines():
        if "─" in line or not line.strip():
            continue
        match = HERMES_PROFILE_RE.match(line)
        if not match:
            continue
        profile_id = match.group(1)
        if profile_id in {"Profile", HERMES.primary_id}:
            continue
        model = (match.group(2) or "").split("  ")[0].strip()
        home = Path.home() / ".hermes" / "profiles" / profile_id
        # A profile's SOUL.md opens with the same "# Name 🚚 — Role" heading an
        # OpenClaw IDENTITY.md does. Use it when it's there; otherwise fall
        # back to the bare profile id, which is all `profile list` knows.
        soul = home / "SOUL.md"
        text = _read(soul) if soul.is_file() else ""
        name, emoji, role = parse_identity(text) if text else ("", "", "")
        if name == "Sub-agent":          # no heading — parse_identity's placeholder
            name = ""
        if role == "Sub-agent":          # SOULs carry no **Role:** line; the heading's right half is the role
            heading = HEADING_RE.search(text)
            halves = re.split(r"\s+[—–-]\s+", heading.group(1).strip(), maxsplit=1) if heading else []
            role = halves[1] if len(halves) == 2 else ""
        out.append(
            Subagent(
                id=profile_id,
                engine=HERMES.id,
                name=name or profile_id.replace("-", " ").title(),
                emoji=emoji if name and emoji != "🤖" else "⚡",
                role=role or f"isolated Hermes profile{f' · {model}' if model else ''}",
                source=str(soul if name else home),
            )
        )
    return out


_ROSTERS = {
    OPENCLAW.id: _openclaw_roster,
    CLAUDE.id: _claude_roster,
    HERMES.id: _hermes_roster,
}


def roster(engine: str) -> list[Subagent]:
    """Who you can address on one commander. Always non-empty: the commander
    itself is the first entry even when it has no sub-agents at all."""
    return _ROSTERS[get(engine).id]()


def full_roster() -> list[Subagent]:
    return [agent for commander in ALL for agent in roster(commander.id)]


def resolve(engine: str, agent_id: str) -> Subagent:
    """The named sub-agent, or the commander itself if the name is unknown.

    Falling back rather than raising is deliberate: an agent can be renamed or
    deleted between opening a session and sending into it, and answering as the
    commander is far better than dropping the message.
    """
    agents = roster(engine)
    return next((a for a in agents if a.id == agent_id), agents[0])


# --------------------------------------------------------------------------
# invocation
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Turn:
    """A ready-to-run subprocess call for one message."""

    argv: list[str]
    cwd: str
    env: dict[str, str]


def openclaw_session_key(agent: Subagent, session_key: str) -> str:
    """The one spelling of an OpenClaw thread's key.

    Both the ACP bridge and the argv fallback have to send the same string or a
    fallback turn would silently open a second, empty conversation alongside
    the real one.
    """
    return f"agent:{agent.id}:{session_key}"


def build_turn(engine: str, agent: Subagent, message: str,
               native_session_id: str, session_key: str) -> Turn:
    """The native invocation for one turn, resumed where the CLI supports it.

    `session_key` is the deck's own stable handle for the thread. OpenClaw
    takes one directly; the other two hand back an id of their own after the
    first turn, which is what `native_session_id` carries on every turn after.
    """
    settings = get_settings()
    commander = get(engine)
    binary = commander.binary()
    workspace = str(settings.workspace_path)

    if commander is OPENCLAW:
        # The streaming path is the ACP bridge (see `acp.py`); this argv is the
        # fallback used when the bridge cannot be reached, so the tab degrades
        # to a slow answer rather than to no answer.
        #
        # `--session-key` is OpenClaw's own threading primitive, so continuity
        # needs no round-trip: the same key is the same conversation, from the
        # very first turn — and it is the *same* key the bridge is given, so
        # falling back does not start the thread over.
        argv = [binary, "agent", "--agent", agent.id, "--json",
                "--session-key", openclaw_session_key(agent, session_key),
                "-m", message]
        if settings.openclaw_model:
            argv += ["--model", settings.openclaw_model]
        return Turn(argv, workspace, {})

    if commander is CLAUDE:
        # stream-json, not json: the deck should show the answer being written
        # the way `claude -p` does in a terminal, rather than a spinner for the
        # length of a build turn and then a wall of text.
        #
        # No --dangerously-skip-permissions. A turn fired from a chat box
        # should answer and read, not silently rewrite the filesystem; anything
        # needing approval surfaces as a denial in the reply instead.
        # --permission-prompts none is the explicit form of the posture this
        # already had. The default is "host", and there is no host on the far
        # end of a chat box — so anything needing approval used to sit there
        # unanswered until the turn timed out, which reads as a hang rather
        # than as a refusal. "none" denies it immediately and the denial comes
        # back as text the reader can actually see.
        # "@opus …" / "@sonnet …" / "@haiku …" / "@fable …" picks the model
        # for this one turn. Without it the agent's own frontmatter `model`
        # applies (or claude_model, when that is set).
        turn_model, message = model_directive(message)
        argv = [binary, "-p", message,
                "--output-format", "stream-json", "--include-partial-messages",
                "--permission-prompts", "none", "--verbose"]
        if not agent.primary:
            argv += ["--agent", agent.id]
        if native_session_id:
            argv += ["--resume", native_session_id]
        if turn_model or settings.claude_model:
            argv += ["--model", turn_model or settings.claude_model]
        return Turn(argv, workspace, {})

    # Hermes: `chat -Q`, not the top-level `--oneshot`.
    #
    # --oneshot is built for pipes and starts from nothing every time — it
    # accepts --resume and silently ignores the history, which reads as an
    # agent with amnesia rather than as an error. `chat -Q` genuinely rehydrates
    # the session, and --pass-session-id makes it print the id it used, so a
    # thread becomes resumable from its very first turn.
    # No -Q. Quiet mode's own help says it suppresses "banner, spinner, and
    # tool previews", and those previews are the only sign of life Hermes gives
    # during a turn — suppressing them is what made this tab a spinner. Oneshot
    # behaviour is implied on non-TTY stdio, so the flag bought nothing else,
    # and `chat` keeps the --resume rehydration that top-level --oneshot lacks.
    argv = [binary, "chat", "--pass-session-id", "--in", workspace, "-q", message]
    # A profile is Hermes' unit of isolation, and `-p <name>` is what selects
    # it. This used to set HERMES_PROFILE in the environment instead, but Hermes
    # only reads that variable for labelling, never to pick a profile. So every
    # sub-agent turn (Key, Courier, Scheduler) ran as the default Hermes, with
    # none of its own SOUL, memory or model. Found 2026-09-22.
    if not agent.primary:
        argv[1:1] = ["-p", agent.id]
    if native_session_id:
        argv += ["--resume", native_session_id]
    # The deck's provider/model apply to every Hermes turn, sub-agent profiles
    # included.
    #
    # This used to be `if agent.primary`, on the reasoning that a profile owns
    # its own model in config.yaml and a --model flag here would override it.
    # True in principle, but every profile's config.yaml names provider
    # `deepseek` and not one of them has a deepseek credential — their
    # auth.json files carry an empty `providers` map, because a cloned profile
    # does not inherit the root's. So "respect the profile's own model" meant
    # every sub-agent turn died on `No usable credentials found for provider
    # 'deepseek'`, and died *quietly*: Hermes prints that as ordinary output
    # and exits 0, so the deck recorded a 10s `done` turn whose body was the
    # error. Email, Courier, Key and Scheduler were all unusable this way.
    #
    # Verified 2026-09-25: `hermes -p email chat --provider anthropic
    # --model claude-sonnet-4.6` answers normally, the same flags the primary
    # already got. If a profile ever needs its own model again, give it its own
    # setting rather than restoring a rule that silently disables the tab.
    if settings.hermes_provider:
        argv += ["--provider", settings.hermes_provider]
    if settings.hermes_model:
        argv += ["--model", settings.hermes_model]
    return Turn(argv, workspace, {})


# --------------------------------------------------------------------------
# reading a turn as it arrives
# --------------------------------------------------------------------------
class Reader:
    """Turns one commander's stdout into typed events as the lines land.

    A turn is a subprocess, so the difference between "the deck feels like the
    CLI" and "the deck feels like a form submission" is entirely whether the
    reader is told anything before the answer exists. Each commander says
    something different on the way, and none of it used to be shown:

    * **Claude Code** emits newline-delimited stream-json carrying text deltas,
      thinking, every tool call, every tool result, and a final `result` event
      with tokens and dollars.
    * **Hermes** prints tool previews with their timings, then the answer
      inside a box, then a footer with the session id.
    * **OpenClaw** does not come through here at all any more — it is driven
      over the ACP bridge, which is a protocol rather than a text stream.

    Events go to `sink(kind, data)`. Only `delta` becomes the stored reply;
    everything else is the working, which the deck shows above the answer and
    then folds away.
    """

    def __init__(self, engine: str, sink=None) -> None:
        self.commander = get(engine)
        self._sink = sink or (lambda kind, data: None)
        self._lines: list[str] = []
        self._deltas: list[str] = []
        # Claude: tool calls are announced, then their arguments arrive as a
        # separate stream of JSON fragments, then the result comes back later
        # under the same id. Held here so a finished call can be matched up.
        self._tools: dict[str, dict] = {}
        self._open_tool: str = ""
        self._tool_args: list[str] = []
        # Hermes: the answer is whatever is inside the boxes, and the last box
        # is the final word — anything earlier is an interim message.
        self._in_box = False
        self._boxes: list[list[str]] = []
        self._footer = False

    def emit(self, kind: str, data: dict) -> None:
        self._sink(kind, data)

    def feed(self, line: str) -> None:
        self._lines.append(line)
        if self.commander is CLAUDE:
            self._feed_claude(line)
        elif self.commander is HERMES:
            self._feed_hermes(line)

    # -- Claude ------------------------------------------------------------
    def _feed_claude(self, line: str) -> None:
        line = line.strip()
        if not line.startswith("{"):
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(event, dict):
            return
        kind = event.get("type")

        if kind == "stream_event":
            self._claude_stream_event(event.get("event") or {})
        elif kind == "user":
            self._claude_tool_results(event)
        elif kind == "system" and event.get("subtype") == "status":
            status = str(event.get("status") or "")
            if status:
                self.emit("status", {"text": status})
        elif kind == "result":
            usage = event.get("usage") or {}
            self.emit("usage", {
                "cost_usd": event.get("total_cost_usd"),
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "cache_read_tokens": usage.get("cache_read_input_tokens"),
                "duration_api_ms": event.get("duration_api_ms"),
            })

    def _claude_stream_event(self, inner: dict) -> None:
        kind = inner.get("type")

        if kind == "content_block_start":
            block = inner.get("content_block") or {}
            if block.get("type") == "tool_use":
                tool_id = str(block.get("id") or "")
                name = str(block.get("name") or "tool")
                self._tools[tool_id] = {"name": name}
                self._open_tool = tool_id
                self._tool_args = []
                self.emit("tool", {"id": tool_id, "title": name, "status": "running"})
            return

        if kind == "content_block_delta":
            delta = inner.get("delta") or {}
            delta_type = delta.get("type")
            if delta_type == "text_delta":
                text = str(delta.get("text") or "")
                if text:
                    self._deltas.append(text)
                    self.emit("delta", {"text": text})
            elif delta_type == "thinking_delta":
                self.emit("thought", {"text": str(delta.get("thinking") or "")})
            elif delta_type == "input_json_delta":
                # A tool's arguments arrive as JSON fragments. They are only
                # worth showing once complete, so they are buffered and
                # summarised at content_block_stop.
                self._tool_args.append(str(delta.get("partial_json") or ""))
            return

        if kind == "content_block_stop" and self._open_tool:
            tool = self._tools.get(self._open_tool) or {}
            summary = _tool_summary(tool.get("name", ""), "".join(self._tool_args))
            tool["summary"] = summary
            self.emit("tool", {
                "id": self._open_tool, "title": tool.get("name", "tool"),
                "detail": summary, "status": "running",
            })
            self._open_tool = ""
            self._tool_args = []

    def _claude_tool_results(self, event: dict) -> None:
        """A tool coming back — the half a spinner can never show."""
        content = ((event.get("message") or {}).get("content")) or []
        if not isinstance(content, list):
            return
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            tool_id = str(block.get("tool_use_id") or "")
            tool = self._tools.get(tool_id) or {}
            failed = bool(block.get("is_error"))
            self.emit("tool", {
                "id": tool_id,
                "title": tool.get("name", "tool"),
                "detail": tool.get("summary", ""),
                "status": "failed" if failed else "done",
                "result": _short(_result_text(block), 160),
            })

    # -- Hermes ------------------------------------------------------------
    def _feed_hermes(self, raw: str) -> None:
        line = visible_line(raw)
        if not line.strip():
            return

        if HERMES_BOX_OPEN_RE.match(line):
            self._in_box = True
            self._boxes.append([])
            return
        if HERMES_BOX_CLOSE_RE.match(line) and self._in_box:
            self._in_box = False
            # Only the final box is the answer, so an earlier one is shown as
            # working rather than silently dropped.
            if len(self._boxes) > 1:
                text = "\n".join(self._boxes[-2]).strip()
                if text:
                    self.emit("interim", {"text": text})
            self._republish_answer()
            return
        if self._in_box:
            self._boxes[-1].append(line)
            self._republish_answer()
            return

        tool = HERMES_TOOL_RE.match(line)
        if tool:
            self.emit("tool", _hermes_tool_event(tool.group(1)))
            return
        stripped = line.strip()
        if stripped.startswith(HERMES_FOOTER_PREFIXES):
            self._footer = True
        if self._footer or HERMES_STATUS_RE.match(line) or stripped.startswith("─"):
            return
        self.emit("status", {"text": _short(line, 120)})

    def _republish_answer(self) -> None:
        """Hermes writes the answer as whole lines rather than as deltas, so
        the "text so far" is recomputed and the change sent as a delta."""
        text = "\n".join(self._boxes[-1]).strip() if self._boxes else ""
        already = "".join(self._deltas)
        if text.startswith(already) and text != already:
            addition = text[len(already):]
            self._deltas.append(addition)
            self.emit("delta", {"text": addition})
        elif text != already:
            # The box was rewritten rather than appended to; replace outright.
            self._deltas = [text]
            self.emit("replace", {"text": text})

    # -- finishing ---------------------------------------------------------
    def text_so_far(self) -> str:
        """What has been shown as the answer, for a turn that is cut short."""
        return "".join(self._deltas).strip()

    def finish(self, stderr: str, returncode: int) -> tuple[str, str, str]:
        """`(text, native_session_id, error)` for the completed turn."""
        return parse_reply(self.commander.id, "".join(self._lines), stderr, returncode)


def _short(text: str, limit: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _result_text(block: dict) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            str(c.get("text") or "") for c in content if isinstance(c, dict)
        )
    return ""


def _tool_summary(name: str, raw_args: str) -> str:
    """The one argument worth showing next to a tool's name.

    A terminal shows `Bash(git status)`, not the whole JSON payload — the point
    is to recognise what the agent is doing at a glance.
    """
    try:
        args = json.loads(raw_args) if raw_args.strip() else {}
    except json.JSONDecodeError:
        return ""
    if not isinstance(args, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "query", "url", "prompt"):
        if args.get(key):
            return _short(args[key], 120)
    return _short(", ".join(f"{k}={v}" for k, v in list(args.items())[:2]), 120)


def _hermes_tool_event(body: str) -> dict:
    """One "┊" preview line turned into a tool event.

    Two shapes: "⚡ preparing mcp__memory…" while a call is being set up, and
    "🧠 memory  +user: …  0.0s" once it has run.
    """
    text = body.strip()
    if text.lower().startswith("⚡ preparing") or "preparing" in text[:20].lower():
        name = text.split("preparing", 1)[-1].strip().rstrip("…").strip()
        return {"id": name, "title": name or "tool", "status": "running"}
    parts = text.split()
    # The line leads with a glyph, then the tool, then what it did: keep the
    # tool as the label and only what follows as the detail, or the deck prints
    # the name twice on the same row.
    leads_with_glyph = len(parts) > 1 and len(parts[0]) <= 2
    name = parts[1] if leads_with_glyph else (parts[0] if parts else "tool")
    detail = " ".join(parts[2:] if leads_with_glyph else parts[1:])
    return {"id": name, "title": name, "detail": _short(detail, 140), "status": "done"}


def _hermes_boxes(stdout: str) -> list[str]:
    """Every "╭─ ⚕ Hermes ─╮ … ╰─╯" block, in order."""
    boxes: list[str] = []
    current: list[str] | None = None
    for raw in stdout.splitlines():
        line = visible_line(raw)
        if HERMES_BOX_OPEN_RE.match(line):
            current = []
            continue
        if HERMES_BOX_CLOSE_RE.match(line) and current is not None:
            boxes.append("\n".join(current).strip())
            current = None
            continue
        if current is not None:
            current.append(line)
    if current:
        boxes.append("\n".join(current).strip())
    return [b for b in boxes if b]


def _hermes_split(stdout: str) -> tuple[str, str]:
    """`(session_id, answer)` out of a `hermes chat` run.

    Now that -Q is gone the answer is whatever sits inside the last box: Hermes
    may speak once before reaching for a tool and again afterwards, and it is
    the last word that is the reply. The earlier ones are surfaced live as
    interim messages rather than concatenated into the stored answer.

    Both older shapes are still honoured as fallbacks — the announced
    `session_id:` line, and the strip-the-status-glyphs behaviour that came
    before it — so a Hermes that stops drawing boxes degrades to a plain answer
    instead of to an empty one.
    """
    match = HERMES_SESSION_LINE_RE.search(stdout)
    footer = HERMES_FOOTER_SESSION_RE.search(strip_ansi(stdout))
    native = match.group(1) if match else (footer.group(1) if footer else "")

    boxes = _hermes_boxes(stdout)
    if boxes:
        return native, boxes[-1]

    if match:
        return native, stdout[match.end():].strip()

    lines = [visible_line(line) for line in stdout.splitlines()]
    # Drop the trailing "Resume this session with: …" footer, which is written
    # for a human at a prompt and is not part of the reply.
    for index, line in enumerate(lines):
        if line.startswith("Resume this session with"):
            lines = lines[:index]
            break
    start = 0
    for index, line in enumerate(lines):
        if line.strip() and not HERMES_STATUS_RE.match(line):
            start = index
            break
    return native, "\n".join(lines[start:]).strip()


# --------------------------------------------------------------------------
# reply parsing
# --------------------------------------------------------------------------
def _json_tail(stdout: str) -> dict | None:
    """The JSON object in stdout, which the CLIs prefix with banners."""
    start = stdout.find("{")
    if start == -1:
        return None
    try:
        parsed = json.loads(stdout[start:])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _first_line_of(stderr: str, stdout: str, returncode: int, who: str) -> str:
    detail = (stderr or stdout).strip().splitlines()
    return detail[-1][:300] if detail else f"{who} exited {returncode}"


def parse_reply(engine: str, stdout: str, stderr: str, returncode: int) -> tuple[str, str, str]:
    """`(text, native_session_id, error)` from a finished turn."""
    commander = get(engine)

    if commander is HERMES:
        # `chat -Q` writes a couple of status lines, then `session_id: <id>`,
        # then the answer as plain text. Failures arrive on the same stream
        # ("HTTP 404: Model 'deepseek' not found"), so an HTTP-shaped line is
        # the only error signal there is on a zero exit.
        native, text = _hermes_split(stdout)
        if returncode != 0 and not text:
            return "", native, _first_line_of(stderr, stdout, returncode, "hermes")
        if HTTP_ERROR_RE.match(text):
            return "", native, text.splitlines()[0][:300]
        return text, native, "" if text else "hermes returned an empty reply"

    if commander is CLAUDE:
        # stream-json is newline-delimited, and the `result` event is last.
        # The session id shows up in the very first event, so it is worth
        # keeping even from a run that then failed — a resumable thread is
        # better than a lost one.
        native, result = "", None
        for line in stdout.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            native = native or str(event.get("session_id") or "")
            if event.get("type") == "result":
                result = event
        if result is None:
            return "", native, _first_line_of(stderr, stdout, returncode, commander.name)
        if result.get("is_error"):
            return "", native, str(result.get("result") or "claude reported an error")[:300]
        text = str(result.get("result") or "").strip()
        return text, native, "" if text else "claude returned an empty reply"

    data = _json_tail(stdout)
    if data is None:
        return "", "", _first_line_of(stderr, stdout, returncode, commander.name)

    # OpenClaw wraps the answer in a payload list; the run id is per-turn, not
    # per-conversation, so continuity stays with the session key we sent.
    payloads = (data.get("result") or {}).get("payloads") or []
    for item in payloads:
        if isinstance(item, dict) and str(item.get("text") or "").strip():
            return str(item["text"]).strip(), "", ""
    summary = str(data.get("summary") or "").strip()
    if summary:
        return summary, "", ""
    return "", "", f"the agent returned no text (status {data.get('status')})"


def latest_hermes_session(workspace: str) -> str:
    """The id Hermes just used, read back from its own session store.

    Hermes' --oneshot prints the answer and nothing else — by design, since it
    is built for pipes — so the only way to learn the conversation id is to ask
    the store which session it most recently touched for this workspace. Best
    effort: without it a Hermes thread still works, it just starts cold each
    turn, so no failure here is worth surfacing.
    """
    if not HERMES.available():
        return ""
    try:
        proc = subprocess.run(
            [HERMES.binary(), "sessions", "list", "--source", "cli",
             "--workspace", Path(workspace).name, "--limit", "1"],
            capture_output=True, text=True, timeout=PROFILE_LIST_TIMEOUT, cwd=workspace,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    match = HERMES_SESSION_ID_RE.search(proc.stdout or "")
    return match.group(1) if match else ""
