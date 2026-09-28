"""Blanco OS — the personal agentic operating system API.

One local FastAPI service that fuses the second-brain vault, the OpenClaw agent
fleet, the machine's own health, and the venture ledger into a single typed
surface a dashboard can be built on.

    uvicorn app.main:app --port 8800
    http://127.0.0.1:8800/docs
"""

from __future__ import annotations

import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import get_settings
from app.deps import init_db
from app.routers import (
    agents,
    bridge,
    certs,
    chat,
    command,
    credit,
    email,
    finance,
    jobs,
    journal,
    biz,
    knowledge,
    meta,
    money,
    notepad,
    nowplaying,
    stream,
    systems,
    tasks,
    timer,
)

DESCRIPTION = """
Personal OS for Blanco (5lanxo). Fourteen modules over one API:

| Module | Prefix | Backed by |
|---|---|---|
| Command Deck | `/api/command` | everything, fused |
| Tasks & Calendar | `/api/tasks`, `/api/events`, `/api/notes` | vault `todo-data.json` |
| Money & Ventures | `/api/money`, `/api/ventures` | OS database |
| Finance | `/api/finance` | OS database + vault `Debt-Tracker.md` |
| Credit | `/api/credit` | OS database |
| Cert Track | `/api/certs` | vault roadmap markdown |
| Journal & Mood | `/api/journal` | vault `diary.json` |
| Inbox | `/api/email` | Gmail via the categorizer's rules |
| Focus Timer | `/api/timer` | OS database |
| Notepad | `/api/notepad` | OS database |
| Agent Fleet | `/api/agents` | roster + live probes |
| Systems | `/api/systems` | local probes |
| Knowledge | `/api/knowledge` | vault markdown |
| Your Business | `/api/biz` | OS database |
| Your Business Bridge | `/api/bridge` | OS database |

Start at **`GET /api/command/brief`** — it is the whole home screen in one call.
**`GET /api/system`** enumerates the modules so the shell can build its own nav.
"""


def _warm_probes() -> None:
    """Prime the service-status cache and the OS page cache for the vault.

    Deliberately touches no database: the shared sqlite handle can be closed
    out from under a background thread (tests do exactly that), and using it
    afterwards segfaults the interpreter. Everything slow here is filesystem
    work against /mnt/c anyway — the DB queries are microseconds.
    """
    try:
        from app import db
        from app.services import email_service, systems_service

        systems_service.services()

        # The two cache keys the deck actually asks for: limit=25 from the
        # daily brief, limit=30 from the Inbox view. Own connection — see
        # email_service._spawn_refresh for why sharing one is unsafe.
        conn = db.connect()
        try:
            for mail_limit in (25, 30):
                email_service.fetch(conn, limit=mail_limit, unread_only=True)
        finally:
            conn.close()

        # The Knowledge view walks every .md in the vault — 7s cold.
        from app.vault import iter_notes

        iter_notes(get_settings().vault_path)

        settings = get_settings()
        for attr in (
            "todo_data_file", "todo_markdown_file", "diary_file",
            "cert_roadmap_file", "credit_playbook_file", "mood_trends_file",
            "active_focus_file", "system_map_file",
        ):
            path = getattr(settings, attr, None)
            if isinstance(path, Path) and path.is_file():
                path.read_bytes()
    except Exception:  # noqa: BLE001 — a cold cache is not worth a crash
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = init_db()
    # Restore the debt ledger from the vault if the table is empty. Normally a
    # no-op — migration 011 seeds it — but it means a wiped database comes back
    # with what Blanco actually owes rather than an empty tab. One COUNT when
    # there is nothing to do, so it stays off the /mnt/c path.
    try:
        from app.services import debt_vault_sync

        debt_vault_sync.import_debts(conn)
    except Exception:  # noqa: BLE001 — a stale ledger is not worth a failed boot
        pass
    # First touch of the vault crosses the /mnt/c mount cold and costs seconds.
    # Pay it here, off the request path, so the first page load of the day is
    # fast instead of being the one that warms the cache.
    threading.Thread(target=_warm_probes, daemon=True).start()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Blanco OS",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_tags=[
            {"name": "command", "description": "Daily brief, focus, alerts."},
            {"name": "tasks", "description": "Board, calendar, quick notes."},
            {"name": "money", "description": "Income streams and the ledger."},
            {"name": "finance", "description": "Debts, payoff plan, investments, cash flow. Advisory only."},
            {"name": "credit", "description": "Scores, tradelines, disputes, and the build plan."},
            {"name": "certs", "description": "Certification roadmap progress."},
            {"name": "journal", "description": "Entries, streaks, mood series."},
            {"name": "email", "description": "Inbox, categorised and ranked by importance."},
            {"name": "timer", "description": "Work / break sessions."},
            {"name": "notepad", "description": "Scratch notes behind the popout widget."},
            {"name": "chat", "description": "Messenger — talk to Sweet Jones and the sub-agents."},
            {"name": "agents", "description": "The agent fleet."},
            {"name": "systems", "description": "Infrastructure health."},
            {"name": "knowledge", "description": "Second-brain search."},
            {"name": "biz", "description": "The work OS — three divisions, their ventures and the deal pipeline."},
            {"name": "bridge", "description": "One-way notices from Your Business."},
            {"name": "stream", "description": "Server-sent live feed."},
            {"name": "meta", "description": "Self-description and health."},
        ],
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    for router in (
        meta.router,
        command.router,
        tasks.router,
        tasks.events_router,
        tasks.notes_router,
        money.router,
        money.ventures_router,
        finance.router,
        credit.router,
        certs.router,
        journal.router,
        email.router,
        timer.router,
        notepad.router,
        jobs.router,
        nowplaying.router,
        chat.router,
        agents.router,
        systems.router,
        knowledge.router,
        biz.router,
        bridge.router,
        stream.router,
    ):
        app.include_router(router)

    # The command deck. Mounted last so every /api route still wins, and only
    # when the directory exists — the API is fully usable headless.
    web_dir = Path(__file__).resolve().parent.parent / "web"
    if web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    cfg = get_settings()
    uvicorn.run("app.main:app", host=cfg.host, port=cfg.port, reload=False)
