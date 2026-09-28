"""The agent roster — who exists, and whether they are up.

Two sources, deliberately:

* **Fixed specs** for the pieces whose shape doesn't change — the always-on
  services with their own cron schedules and log files.
* **Discovery**, via `commanders`, for the three CLI commanders and every
  sub-agent on their rosters. Blanco adds a sub-agent by creating a directory,
  a markdown file or a Hermes profile, so hardcoding any of them here would go
  stale the day he adds one.

The console (`chat_service`) and this view read the same rosters, so an agent
you can see here is an agent you can talk to there.

Live signals come from cheap local probes and heartbeat-file mtimes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from app import schemas
from app.config import get_settings
from app.services import commanders, systems_service
from app.vault import modified_at

# Which commander a sub-agent answers to, as a one-line prefix on its role, so
# the roster reads as three chains of command rather than one flat list.
COMMANDER_LABEL = {c.id: c.name for c in commanders.ALL}

# Where `hermes cron --script` resolves a job's script name.
HERMES_SCRIPTS = Path.home() / ".hermes" / "scripts"


@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    emoji: str
    role: str
    kind: str
    engine: str = ""
    model: str = ""
    workspace: str = ""
    channel: str = ""
    schedules: tuple[tuple[str, str, str], ...] = ()
    heartbeat_path: str = ""
    #: ISO timestamp of the last run, for workers whose scheduler records it
    #: rather than leaving a file behind.
    last_run: str = ""
    detail: str = ""


def _commander_specs() -> list[AgentSpec]:
    """The three commanders, each followed by its own sub-agents.

    Grouping is the point: these are three separate chains of command, and a
    flat alphabetical list of eleven agents hid which one you were addressing.
    """
    settings = get_settings()
    vault = str(settings.vault_path)
    # Details that belong to Blanco's setup rather than to the CLI itself, so
    # discovery stays honest and the colour lives in one dictionary.
    extras = {
        "openclaw": dict(
            channel="Discord · Your Business guild",
            schedules=(("Weekly self-audit", "0 9 * * 1", "Mondays 9:00 AM CDT"),),
            detail="Routes every question to the right sub-agent and owns the daily brief.",
        ),
        "claude": dict(
            heartbeat_path=f"{vault}/02-areas/agent-ops/Claude-code-hand-off.md",
            detail="Git management, script upgrades and full-stack builds.",
        ),
        "hermes": dict(
            detail="Tools, skills and memory in the gateway. Each profile is its own instance.",
        ),
    }

    out: list[AgentSpec] = []
    for commander in commanders.ALL:
        if not commander.available():
            continue
        for agent in commanders.roster(commander.id):
            base = extras.get(commander.id, {}) if agent.primary else {}
            out.append(
                AgentSpec(
                    id=f"{commander.id}:{agent.id}",
                    name=agent.name,
                    emoji=agent.emoji,
                    role=agent.role,
                    kind="commander" if agent.primary else "subagent",
                    engine=commander.id,
                    workspace=agent.source,
                    **base,
                )
            )
    return out


def _service_specs() -> list[AgentSpec]:
    """The workers that run on a schedule rather than on a conversation.

    Two of them are Blanco's own long-lived scripts; the rest are whatever
    Hermes cron is holding, read live. Hermes jobs used to be invisible here —
    the email categorizer was hardcoded with the crontab line it no longer runs
    on, and every job added since simply did not exist as far as the deck was
    concerned.
    """
    ws = str(get_settings().workspace_path)
    jobs = systems_service.hermes_jobs()

    def take(*needles: str) -> dict | None:
        """Pull the job matching one of `needles` out of `jobs`, once."""
        for job in list(jobs):
            haystack = f"{job.get('name', '')} {job.get('script', '')}".lower()
            if any(n.lower() in haystack for n in needles):
                jobs.remove(job)
                return job
        return None

    def schedule_of(job: dict, label: str) -> tuple[tuple[str, str, str], ...]:
        display = job.get("schedule_display") or "hermes cron"
        return ((label, display, display),)

    out: list[AgentSpec] = []

    digest = take("email organizer", "email-digest")
    out.append(
        AgentSpec(
            id="email-categorizer",
            name="Email Categorizer",
            emoji="📧",
            role="Sorts inbound mail into 13 categories, flags VIP senders",
            kind="service",
            workspace=f"{ws}/email-categorizer",
            channel=(digest or {}).get("deliver") or "Discord · #personal-emails",
            schedules=schedule_of(digest, "Inbox sweep") if digest
                      else (("Inbox sweep", "0 */2 * * *", "every 2 hours"),),
            last_run=(digest or {}).get("last_run_at") or "",
            detail="Run by Hermes cron." if digest else "",
        )
    )
    out.append(
        AgentSpec(
            id="dispatch-bot-bot",
            name="Dispatch Bot",
            emoji="🦅",
            role="Posts freight loads to Discord",
            kind="service",
            workspace=f"{ws}/dispatch_bot",
            schedules=(("Watchdog", "*/5 * * * *", "every 5 minutes"),),
        )
    )

    # Everything else Hermes is running on a timer, named as Hermes named it.
    for job in jobs:
        job_id = str(job.get("id") or "")
        script = job.get("script") or ""
        deliver = job.get("deliver") or ""
        out.append(
            AgentSpec(
                id=f"hermes-cron:{job_id}",
                name=str(job.get("name") or script or job_id),
                emoji="⏱️",
                role="Hermes cron job" + (f" · {script}" if script else " · agent prompt"),
                kind="service",
                workspace=str(HERMES_SCRIPTS / script) if script else "",
                channel=deliver,
                schedules=schedule_of(job, "Run"),
                last_run=job.get("last_run_at") or "",
                detail=("no-agent · script output delivered verbatim"
                        if job.get("no_agent") else "runs as a Hermes agent turn"),
            )
        )
    return out


def roster() -> schemas.AgentRoster:
    settings = get_settings()
    probes = settings.probes_enabled

    host, _, port = settings.openclaw_url.removeprefix("http://").partition(":")
    orchestrator_up = (
        systems_service.port_open(host, int(port or 80), settings.probe_timeout_seconds)
        if probes
        else False
    )
    bot_up = systems_service.process_running("dispatch-bot") if probes else False

    specs = [*_commander_specs(), *_service_specs()]

    agents: list[schemas.Agent] = []
    for spec in specs:
        status = _status_for(spec, orchestrator_up, bot_up, probes)
        detail = spec.detail
        if spec.workspace and not Path(spec.workspace).exists():
            detail = "workspace directory not found"
        agents.append(
            schemas.Agent(
                id=spec.id,
                name=spec.name,
                emoji=spec.emoji,
                role=spec.role,
                model=spec.model,
                engine=spec.engine,
                kind=spec.kind,
                status=status,
                workspace=spec.workspace,
                channel=spec.channel,
                schedules=[
                    schemas.AgentSchedule(label=label, cron=cron, human=human)
                    for label, cron, human in spec.schedules
                ],
                last_seen=spec.last_run
                          or (modified_at(Path(spec.heartbeat_path)) if spec.heartbeat_path else None),
                detail=detail,
            )
        )

    return schemas.AgentRoster(
        probed=probes,
        online=sum(1 for a in agents if a.status == "online"),
        standby=sum(1 for a in agents if a.status == "standby"),
        scheduled=sum(1 for a in agents if a.status == "scheduled"),
        total=len(agents),
        agents=agents,
    )


def _status_for(spec: AgentSpec, orchestrator_up: bool, bot_up: bool, probes: bool) -> str:
    """What to show under an agent's name.

    Only OpenClaw has a resident process to probe. Claude Code and Hermes are
    CLIs that exist between turns as a binary and a session store, so "standby"
    is the honest reading for them and their sub-agents — the roster only lists
    a commander at all when its binary is actually installed.
    """
    if not probes:
        return "unknown"
    if spec.id == "dispatch-bot-bot":
        return "online" if bot_up else "offline"
    if spec.engine == commanders.OPENCLAW.id:
        if spec.kind == "commander":
            return "online" if orchestrator_up else "offline"
        # Domain agents are spawned per conversation, not resident.
        return "standby" if orchestrator_up else "offline"
    if spec.engine:
        return "standby"
    if spec.kind == "service":
        return "scheduled"
    return "unknown"


def get_agent(agent_id: str) -> schemas.Agent | None:
    return next((a for a in roster().agents if a.id == agent_id), None)
