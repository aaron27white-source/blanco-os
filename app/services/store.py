"""OS-owned state: focus, alerts, activity log, daily signals."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app import schemas
from app.config import get_settings
from app.vault import read_text


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def local_today() -> str:
    """Blanco's calendar date, not UTC's.

    Timestamps are stored in UTC, but "today" has to mean his day — after
    7 PM Chicago it is already tomorrow in UTC, and a work block logged at
    8 PM belongs to the day he actually worked it.
    """
    return datetime.now(_local_zone()).date().isoformat()


def local_day_bounds_utc(day: str | None = None) -> tuple[str, str]:
    """UTC half-open range [start, end) covering one local calendar day.

    Returned as 'YYYY-MM-DD HH:MM:SS' strings so they compare directly against
    SQLite columns written by datetime('now') and our own isoformat(sep=' ').
    """
    zone = _local_zone()
    target = date.fromisoformat(day) if day else datetime.now(zone).date()
    start_local = datetime.combine(target, time.min, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    fmt = "%Y-%m-%d %H:%M:%S"
    return (
        start_local.astimezone(timezone.utc).strftime(fmt),
        end_local.astimezone(timezone.utc).strftime(fmt),
    )


def _local_zone() -> ZoneInfo:
    try:
        return ZoneInfo(get_settings().timezone)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


# --- activity ---------------------------------------------------------------


def log(conn: sqlite3.Connection, module: str, verb: str, subject: str, **meta) -> None:
    conn.execute(
        "INSERT INTO activity_log (module, verb, subject, meta_json) VALUES (?, ?, ?, ?)",
        (module, verb, subject, json.dumps(meta)),
    )
    conn.commit()


def recent_activity(conn: sqlite3.Connection, limit: int = 50) -> list[schemas.ActivityItem]:
    rows = conn.execute(
        "SELECT * FROM activity_log ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [
        schemas.ActivityItem(
            id=r["id"],
            module=r["module"],
            verb=r["verb"],
            subject=r["subject"],
            meta=json.loads(r["meta_json"] or "{}"),
            created_at=r["created_at"],
        )
        for r in rows
    ]


# --- focus ------------------------------------------------------------------


def get_focus(conn: sqlite3.Connection) -> schemas.Focus:
    row = conn.execute("SELECT * FROM focus WHERE id = 1").fetchone()
    if row and row["headline"]:
        return schemas.Focus(
            headline=row["headline"],
            detail=row["detail"],
            horizon=row["horizon"],
            set_at=row["set_at"],
            source="os",
        )
    return _focus_from_active_md()


def set_focus(conn: sqlite3.Connection, update: schemas.FocusUpdate) -> schemas.Focus:
    conn.execute(
        """
        INSERT INTO focus (id, headline, detail, horizon, set_at)
        VALUES (1, ?, ?, ?, datetime('now'))
        ON CONFLICT(id) DO UPDATE SET
            headline = excluded.headline,
            detail   = excluded.detail,
            horizon  = excluded.horizon,
            set_at   = excluded.set_at
        """,
        (update.headline, update.detail, update.horizon),
    )
    conn.commit()
    log(conn, "command", "set_focus", update.headline)
    return get_focus(conn)


def _focus_from_active_md() -> schemas.Focus:
    """Fall back to Sweet Jones's ACTIVE.md '🔴 Current Focus' section."""
    text = read_text(get_settings().active_focus_file)
    if not text:
        return schemas.Focus(headline="No focus set", source="none")

    headline, detail_lines = "", []
    in_section = False
    for line in text.splitlines():
        if line.startswith("## ") and "Current Focus" in line:
            in_section = True
            continue
        if in_section:
            if line.startswith("## "):
                break
            stripped = line.strip()
            if not stripped:
                continue
            if not headline:
                headline = stripped.strip("*# ")
            elif stripped.startswith("-"):
                detail_lines.append(stripped.lstrip("- ").strip())

    if not headline:
        return schemas.Focus(headline="No focus set", source="none")
    return schemas.Focus(
        headline=headline,
        detail=" · ".join(detail_lines[:3]),
        horizon="week",
        source="active_md",
    )


# --- alerts -----------------------------------------------------------------


def _alert_row(row: sqlite3.Row) -> schemas.Alert:
    return schemas.Alert(
        id=row["id"],
        source=row["source"],
        severity=row["severity"],
        title=row["title"],
        detail=row["detail"],
        action_label=row["action_label"],
        action_href=row["action_href"],
        acknowledged=bool(row["acknowledged"]),
        created_at=row["created_at"],
    )


def list_alerts(conn: sqlite3.Connection, include_acked: bool = False) -> list[schemas.Alert]:
    sql = "SELECT * FROM alerts"
    if not include_acked:
        sql += " WHERE acknowledged = 0"
    sql += """
        ORDER BY CASE severity WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                 created_at DESC
    """
    return [_alert_row(r) for r in conn.execute(sql).fetchall()]


def raise_alert(conn: sqlite3.Connection, payload: schemas.AlertCreate) -> schemas.Alert:
    """Insert an alert. A repeated dedupe_key re-opens the existing row."""
    if payload.dedupe_key:
        existing = conn.execute(
            "SELECT * FROM alerts WHERE dedupe_key = ?", (payload.dedupe_key,)
        ).fetchone()
        if existing:
            conn.execute(
                """UPDATE alerts SET severity = ?, title = ?, detail = ?,
                   action_label = ?, action_href = ?, acknowledged = 0, acked_at = NULL
                   WHERE id = ?""",
                (
                    payload.severity,
                    payload.title,
                    payload.detail,
                    payload.action_label,
                    payload.action_href,
                    existing["id"],
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM alerts WHERE id = ?", (existing["id"],)).fetchone()
            return _alert_row(row)

    cur = conn.execute(
        """INSERT INTO alerts (source, severity, title, detail, action_label, action_href, dedupe_key)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.source,
            payload.severity,
            payload.title,
            payload.detail,
            payload.action_label,
            payload.action_href,
            payload.dedupe_key,
        ),
    )
    conn.commit()
    log(conn, payload.source, "alert", payload.title, severity=payload.severity)
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _alert_row(row)


def ack_alert(conn: sqlite3.Connection, alert_id: int) -> schemas.Alert | None:
    conn.execute(
        "UPDATE alerts SET acknowledged = 1, acked_at = datetime('now') WHERE id = ?",
        (alert_id,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    return _alert_row(row) if row else None


# --- daily signals ----------------------------------------------------------


def set_signal(conn: sqlite3.Connection, day: str, metric: str, value: float) -> None:
    conn.execute(
        """INSERT INTO daily_signals (day, metric, value) VALUES (?, ?, ?)
           ON CONFLICT(day, metric) DO UPDATE SET value = excluded.value""",
        (day, metric, value),
    )
    conn.commit()


def get_signals(conn: sqlite3.Connection, day: str) -> dict[str, float]:
    rows = conn.execute("SELECT metric, value FROM daily_signals WHERE day = ?", (day,)).fetchall()
    return {r["metric"]: r["value"] for r in rows}


def set_clause(fields: dict, allowed) -> str:
    """A SQL SET clause from a checked column whitelist. Values stay bound.

    Update endpoints build `col = ?` pairs from a Pydantic payload's keys.
    Those keys are fixed by the schema today, but this makes the whitelist
    explicit, so a schema that someday allows extra fields can't turn a JSON
    key into a column name.
    """
    if unknown := set(fields) - set(allowed):
        raise ValueError(f"not updatable: {', '.join(sorted(unknown))}")
    return ", ".join(f"{column} = ?" for column in fields)
