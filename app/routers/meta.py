"""Self-description: the module registry the UI builds its shell from."""

from __future__ import annotations

import sqlite3
import subprocess
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException

from app import __version__, schemas
from app.db import current_version
from app.deps import get_db, settings

router = APIRouter(prefix="/api", tags=["meta"])

MODULES: list[schemas.ModuleInfo] = [
    schemas.ModuleInfo(
        id="command", name="Command Deck", emoji="🛰️",
        tagline="Focus, alerts, and what to do next",
        api_prefix="/api/command", writable=True,
        source="fused from every module + workspace ACTIVE.md",
    ),
    schemas.ModuleInfo(
        id="tasks", name="Tasks & Calendar", emoji="✅",
        tagline="The board, the timeline, the quick notes",
        api_prefix="/api/tasks", writable=True,
        source="vault 02-areas/todo-data.json",
    ),
    schemas.ModuleInfo(
        id="money", name="Money & Ventures", emoji="💰",
        tagline="Every income stream, target vs actual",
        api_prefix="/api/money", writable=True,
        source="blanco_os.db (ventures, venture_events)",
    ),
    schemas.ModuleInfo(
        id="finance", name="Finance", emoji="🏦",
        tagline="Debts, the payoff plan, investments, and where the money went",
        api_prefix="/api/finance", writable=True,
        source="blanco_os.db (debts, investments, transactions) + vault Debt-Tracker.md",
    ),
    schemas.ModuleInfo(
        id="credit", name="Credit", emoji="💳",
        tagline="Scores, tradelines, disputes on the clock, and the build plan",
        api_prefix="/api/credit", writable=True,
        source="blanco_os.db (credit_scores, credit_accounts, credit_disputes, credit_plan_steps)",
    ),
    schemas.ModuleInfo(
        id="certs", name="Cert Track", emoji="🎓",
        tagline="The AI-engineer roadmap and its progress",
        api_prefix="/api/certs", writable=True,
        source="vault Cert-Roadmap-Tracker.md + OS overrides",
    ),
    schemas.ModuleInfo(
        id="journal", name="Journal & Mood", emoji="📓",
        tagline="Daily entries, streaks, 90-day mood series",
        api_prefix="/api/journal", writable=True,
        source="vault 05-journal/diary.json (shared with The Scribe)",
    ),
    schemas.ModuleInfo(
        id="chat", name="Messages", emoji="💬",
        tagline="Talk to Sweet Jones and the sub-agents",
        api_prefix="/api/chat", writable=True,
        source="openclaw agent turns + OS notifications, one timeline",
    ),
    schemas.ModuleInfo(
        id="email", name="Inbox", emoji="📧",
        tagline="Mail that matters, ranked by importance",
        api_prefix="/api/email", writable=True,
        source="Gmail, categorised by the existing email-categorizer rules",
    ),
    schemas.ModuleInfo(
        id="timer", name="Focus Timer", emoji="⏱️",
        tagline="Work and break sessions, tracked server-side",
        api_prefix="/api/timer", writable=True,
        source="blanco_os.db (focus_sessions)",
    ),
    schemas.ModuleInfo(
        id="notepad", name="Notepad", emoji="🗒️",
        tagline="Scratch notes, popped out over any view",
        api_prefix="/api/notepad", writable=True,
        source="blanco_os.db (notepad_notes)",
    ),
    schemas.ModuleInfo(
        id="agents", name="Agent Fleet", emoji="🤖",
        tagline="Sweet Jones and every sub-agent, live",
        api_prefix="/api/agents", writable=False,
        source="static roster + live port/process probes",
    ),
    schemas.ModuleInfo(
        id="systems", name="Systems", emoji="🖥️",
        tagline="Service health, cron, disks, uptime",
        api_prefix="/api/systems", writable=False,
        source="local probes: TCP, pgrep, stat, crontab",
    ),
    schemas.ModuleInfo(
        id="knowledge", name="Knowledge", emoji="🧠",
        tagline="Search and browse the second brain",
        api_prefix="/api/knowledge", writable=False,
        source="vault markdown scan",
    ),
    schemas.ModuleInfo(
        id="bridge", name="Your Business Bridge", emoji="🌉",
        tagline="One-way notices from the work OS",
        api_prefix="/api/bridge", writable=True,
        source="blanco_os.db (bridge_messages)",
    ),
]


@router.get("/system", response_model=schemas.SystemInfo, summary="Who this OS is and what it exposes")
def system_info(db: sqlite3.Connection = Depends(get_db)):
    cfg = settings()
    return schemas.SystemInfo(
        name="Blanco OS",
        version=__version__,
        operator=cfg.operator_name,
        handle=cfg.operator_handle,
        timezone=cfg.timezone,
        schema_version=current_version(db),
        vault_path=str(cfg.vault_path),
        workspace_path=str(cfg.workspace_path),
        server_time=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        modules=MODULES,
    )


@router.get("/modules", response_model=list[schemas.ModuleInfo])
def modules():
    return MODULES


@router.get("/health", tags=["meta"], summary="Liveness probe")
def health(db: sqlite3.Connection = Depends(get_db)):
    return {"status": "ok", "schema_version": current_version(db), "version": __version__}


def _host_allowed(hostname: str | None, patterns: list[str]) -> bool:
    """Exact host, or "*.example.com" for any subdomain of it."""
    if not hostname:
        return False
    host = hostname.lower()
    for pattern in patterns:
        pattern = pattern.lower()
        if pattern.startswith("*."):
            if host == pattern[2:] or host.endswith(pattern[1:]):
                return True
        elif host == pattern:
            return True
    return False


@router.post("/open-in-opera", response_model=schemas.Ack,
             summary="Open a link in Opera rather than the Windows default browser")
def open_in_opera(payload: schemas.OpenUrl):
    cfg = settings()
    parts = urlsplit(payload.url)
    if parts.scheme != "https" or not _host_allowed(parts.hostname, cfg.open_url_hosts):
        raise HTTPException(400, "only https links to an allowed host are opened")
    if not cfg.opera_path.exists():
        raise HTTPException(503, f"Opera not found at {cfg.opera_path}")
    # An argv list with no shell: the URL reaches Opera as one argument and is
    # never parsed as a command, and it was checked against the host list above.
    # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-audit.dangerous-subprocess-use-audit
    subprocess.Popen(  # noqa: S603
        [str(cfg.opera_path), payload.url],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return schemas.Ack(message="opened in Opera")
