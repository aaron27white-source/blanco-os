"""Metric history.

Every metric shipped `trend: "unknown"` because nothing was ever compared to
anything. This snapshots the daily brief's metrics once per day so the deck can
show real movement instead of a placeholder arrow.

Snapshots are idempotent per (day, metric): taking one twice on the same day
updates it rather than creating a second row, so a nightly cron and a manual
call cannot disagree.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app import schemas
from app.services import store

# Metrics where a smaller number is the better number.
LOWER_IS_BETTER = {"open_tasks"}


def snapshot(conn: sqlite3.Connection, metrics: list[schemas.Metric], day: str | None = None) -> int:
    """Record today's values. Returns how many were written."""
    when = day or store.local_today()
    conn.executemany(
        """INSERT INTO metric_history (day, metric_key, value, target, health)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(day, metric_key) DO UPDATE SET
               value = excluded.value, target = excluded.target,
               health = excluded.health, taken_at = datetime('now')""",
        [(when, m.key, m.value, m.target, m.health) for m in metrics],
    )
    conn.commit()
    return len(metrics)


def previous_values(conn: sqlite3.Connection, before: str | None = None) -> dict[str, float]:
    """The most recent value per metric from a day earlier than `before`."""
    cutoff = before or store.local_today()
    rows = conn.execute(
        """SELECT metric_key, value FROM metric_history
            WHERE day < ?
              AND day = (SELECT MAX(day) FROM metric_history h2
                          WHERE h2.metric_key = metric_history.metric_key AND h2.day < ?)""",
        (cutoff, cutoff),
    ).fetchall()
    return {r["metric_key"]: r["value"] for r in rows}


def apply_trends(conn: sqlite3.Connection, metrics: list[schemas.Metric]) -> list[schemas.Metric]:
    """Fill in each metric's `trend` by comparing against the last snapshot."""
    previous = previous_values(conn)
    if not previous:
        return metrics

    for metric in metrics:
        if metric.key not in previous:
            continue
        was = previous[metric.key]
        delta = metric.value - was
        if abs(delta) < 1e-9:
            metric.trend = "flat"
        else:
            rising = delta > 0
            good = not rising if metric.key in LOWER_IS_BETTER else rising
            metric.trend = "up" if rising else "down"
            arrow = "▲" if rising else "▼"
            verdict = "" if good else " ⚠"
            metric.hint = f"{metric.hint} · {arrow} {abs(delta):g} vs last{verdict}".strip(" ·")
    return metrics


def series(conn: sqlite3.Connection, metric_key: str, days: int = 30) -> list[schemas.MetricPoint]:
    since = (date.today() - timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT day, value, target, health FROM metric_history
            WHERE metric_key = ? AND day >= ? ORDER BY day""",
        (metric_key, since),
    ).fetchall()
    return [
        schemas.MetricPoint(
            day=r["day"], value=r["value"], target=r["target"], health=r["health"]
        )
        for r in rows
    ]


def tracked_keys(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT metric_key FROM metric_history ORDER BY metric_key"
    ).fetchall()
    return [r["metric_key"] for r in rows]
