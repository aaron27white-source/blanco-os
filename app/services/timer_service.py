"""Work / break session timer.

State lives in the database, not the browser: reloading the deck must not lose
a countdown, and an agent should be able to tell whether Blanco is heads-down
before it interrupts him.

A session that has run past its `ends_at` is auto-completed on the next read,
so the timer stays correct even if the deck was closed the whole time.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from app import schemas
from app.services import store

WORK_PRESETS = (25, 50, 90)
BREAK_PRESETS = (5, 10, 15)
MAX_MINUTES = 240


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(value: str) -> datetime:
    """SQLite datetime('now') is naive UTC; make it explicit."""
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def _session(row: sqlite3.Row, now: datetime | None = None) -> schemas.FocusSession:
    now = now or _now()
    ends = _parse(row["ends_at"])
    remaining = int((ends - now).total_seconds())
    return schemas.FocusSession(
        id=row["id"],
        kind=row["kind"],
        label=row["label"],
        planned_secs=row["planned_secs"],
        started_at=row["started_at"],
        ends_at=row["ends_at"],
        stopped_at=row["stopped_at"],
        status=row["status"],
        remaining_secs=max(0, remaining) if row["status"] == "running" else 0,
        elapsed_secs=max(0, int((now - _parse(row["started_at"])).total_seconds())),
    )


def _expire_finished(conn: sqlite3.Connection) -> None:
    """Close out any running session whose end time has passed."""
    conn.execute(
        """UPDATE focus_sessions
              SET status = 'completed', stopped_at = ends_at
            WHERE status = 'running' AND ends_at <= ?""",
        (_now().isoformat(sep=" ", timespec="seconds"),),
    )
    # The connection is shared across request threads, so a concurrent commit
    # may already have flushed this one — committing again raises.
    if conn.in_transaction:
        conn.commit()


def current(conn: sqlite3.Connection) -> schemas.FocusSession | None:
    _expire_finished(conn)
    row = conn.execute(
        "SELECT * FROM focus_sessions WHERE status = 'running' LIMIT 1"
    ).fetchone()
    return _session(row) if row else None


def start(conn: sqlite3.Connection, payload: schemas.TimerStart) -> schemas.FocusSession:
    """Start a session, replacing whatever was running."""
    _expire_finished(conn)
    # Starting a new session cancels the old one rather than erroring — the
    # button is a "switch to this" affordance, not a mode.
    conn.execute(
        """UPDATE focus_sessions SET status = 'cancelled', stopped_at = datetime('now')
            WHERE status = 'running'"""
    )

    now = _now()
    secs = int(payload.minutes * 60)
    ends = now + timedelta(seconds=secs)
    cur = conn.execute(
        """INSERT INTO focus_sessions (kind, label, planned_secs, started_at, ends_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            payload.kind,
            payload.label,
            secs,
            now.isoformat(sep=" ", timespec="seconds"),
            ends.isoformat(sep=" ", timespec="seconds"),
        ),
    )
    conn.commit()
    store.log(conn, "focus", f"start {payload.kind}", payload.label or f"{payload.minutes}m",
              minutes=payload.minutes)
    row = conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _session(row)


def stop(conn: sqlite3.Connection, completed: bool = False) -> schemas.FocusSession | None:
    row = conn.execute("SELECT * FROM focus_sessions WHERE status = 'running' LIMIT 1").fetchone()
    if not row:
        return None
    status = "completed" if completed else "cancelled"
    conn.execute(
        "UPDATE focus_sessions SET status = ?, stopped_at = datetime('now') WHERE id = ?",
        (status, row["id"]),
    )
    conn.commit()
    store.log(conn, "focus", status, row["label"] or row["kind"])
    done = conn.execute("SELECT * FROM focus_sessions WHERE id = ?", (row["id"],)).fetchone()
    return _session(done)


def _completed_secs(row: sqlite3.Row) -> int:
    """Actual time served: full plan if completed, else start→stop."""
    if row["status"] == "completed":
        return row["planned_secs"]
    if row["stopped_at"]:
        return max(0, int((_parse(row["stopped_at"]) - _parse(row["started_at"])).total_seconds()))
    return 0


def overview(conn: sqlite3.Connection, history_limit: int = 12) -> schemas.TimerOverview:
    _expire_finished(conn)
    # "Today" is Blanco's calendar day. Rows are UTC, so compare against the
    # UTC window that local day maps to — after 7 PM Chicago they differ.
    start_utc, end_utc = store.local_day_bounds_utc()
    rows = conn.execute(
        """SELECT * FROM focus_sessions
            WHERE started_at >= ? AND started_at < ? ORDER BY id DESC""",
        (start_utc, end_utc),
    ).fetchall()

    work_secs = sum(_completed_secs(r) for r in rows if r["kind"] == "work")
    break_secs = sum(_completed_secs(r) for r in rows if r["kind"] == "break")
    completed = sum(1 for r in rows if r["kind"] == "work" and r["status"] == "completed")

    recent = conn.execute(
        "SELECT * FROM focus_sessions ORDER BY id DESC LIMIT ?", (history_limit,)
    ).fetchall()

    return schemas.TimerOverview(
        active=current(conn),
        work_minutes_today=round(work_secs / 60),
        break_minutes_today=round(break_secs / 60),
        sessions_completed_today=completed,
        work_presets=list(WORK_PRESETS),
        break_presets=list(BREAK_PRESETS),
        history=[_session(r) for r in recent],
    )
