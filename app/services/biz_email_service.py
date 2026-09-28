"""Health of the Your Business inbox monitor.

The monitor is a separate cron'd worker at
`~/.openclaw/workspace/biz-email-monitor`. It pushes what it finds here via
`POST /api/bridge`, so the alerts themselves are already bridge messages. What
the deck cannot see from those alone is whether the monitor is *alive* — and a
dead monitor looks exactly like a quiet inbox.

So this reads the worker's own `state.json` and `reputation.json` and reports
liveness. Read-only, and tolerant of the files not existing: the monitor is an
optional companion, not a dependency, and Blanco OS has to boot without it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app import schemas
from app.config import get_settings

#: Where the worker lives. Overridable so a test never reads the real one.
DEFAULT_MONITOR_DIR = Path.home() / ".openclaw" / "workspace" / "biz-email-monitor"

#: Past this multiple of the poll interval, silence means something is wrong.
STALE_MULTIPLIER = 3


def _monitor_dir() -> Path:
    configured = getattr(get_settings(), "biz_monitor_path", None)
    return Path(configured) if configured else DEFAULT_MONITOR_DIR


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError, ValueError):
        return {}


def health(poll_interval_minutes: int = 30) -> schemas.EmailMonitorHealth:
    directory = _monitor_dir()
    state = _read_json(directory / "state.json")
    reputation = _read_json(directory / "reputation.json")

    installed = directory.is_dir()
    last_poll_at = str(state.get("last_poll_at") or "")
    minutes: float | None = None
    if last_poll_at:
        try:
            last = datetime.fromisoformat(last_poll_at)
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            minutes = round(
                (datetime.now(tz=timezone.utc) - last).total_seconds() / 60.0, 1
            )
        except ValueError:
            minutes = None

    # "Never run" is deliberately not healthy. Reporting green for something
    # that has never executed is the exact failure this endpoint exists to
    # prevent.
    stale_after = poll_interval_minutes * STALE_MULTIPLIER
    if not installed:
        status = "not_installed"
    elif minutes is None:
        status = "never_run"
    elif minutes > stale_after:
        status = "stale"
    elif state.get("last_error"):
        status = "erroring"
    else:
        status = "ok"

    weights: dict = reputation.get("weights") or {}
    top = sorted(weights.items(), key=lambda row: row[1], reverse=True)[:8]

    return schemas.EmailMonitorHealth(
        status=status,
        installed=installed,
        last_poll_at=last_poll_at,
        minutes_since_poll=minutes,
        poll_count=int(state.get("poll_count") or 0),
        alerted_total=len(state.get("seen_ids") or []),
        last_error=str(state.get("last_error") or ""),
        stale_after_minutes=stale_after,
        learned_senders=[
            schemas.LearnedSender(domain=domain, weight=round(float(weight), 2))
            for domain, weight in top
        ],
    )
