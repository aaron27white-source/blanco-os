"""Outbound notifications.

Two channels, both optional and independent:

* **hq_inbox** — drops a markdown file into `workspace/hq/INBOX/`, which Sweet
  Jones already watches per the Agent Connection Protocol. Costs nothing, needs
  no credential, and routes through the agent that already owns Discord.
* **discord** — posts straight to a Discord webhook. Set
  `BLANCO_OS_DISCORD_WEBHOOK_URL` to enable. Worth having *as well*, because it
  still works when OpenClaw is the thing that's down — which is exactly when a
  systems alert matters most.

Everything is deduped: a standing alert is announced once, not every sweep.
Nothing here ever raises; a notification failing must not break a request.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from app.config import get_settings

SEVERITY_RANK = {"info": 0, "warning": 1, "critical": 2}
EMOJI = {"critical": "🚨", "warning": "⚠️", "info": "ℹ️"}
RESEND_AFTER_HOURS = 12.0


def _already_sent(conn: sqlite3.Connection, channel: str, dedupe_key: str) -> bool:
    row = conn.execute(
        """SELECT sent_at FROM notifications
            WHERE channel = ? AND dedupe_key = ? AND ok = 1
            ORDER BY id DESC LIMIT 1""",
        (channel, dedupe_key),
    ).fetchone()
    if not row:
        return False
    try:
        sent = datetime.fromisoformat(row["sent_at"]).replace(tzinfo=timezone.utc)
    except ValueError:
        return True
    age_hours = (datetime.now(timezone.utc) - sent).total_seconds() / 3600
    return age_hours < RESEND_AFTER_HOURS


def _record(conn, channel, dedupe_key, title, body, severity, ok, detail) -> None:
    conn.execute(
        """INSERT INTO notifications (channel, dedupe_key, title, body, severity, ok, detail)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (channel, dedupe_key, title, body, severity, int(ok), detail),
    )
    conn.commit()


# --- channels ---------------------------------------------------------------


def _send_discord(title: str, body: str, severity: str) -> tuple[bool, str]:
    url = get_settings().discord_webhook_url
    if not url:
        return False, "no webhook configured"
    # urlopen also speaks file:// and ftp://. A webhook is only ever https.
    if not url.startswith("https://"):
        return False, "webhook url must be https"

    content = f"{EMOJI.get(severity, 'ℹ️')} **{title}**"
    if body:
        content += f"\n{body}"
    payload = json.dumps({"content": content[:1900], "username": "Blanco OS"}).encode()
    request = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=8) as response:  # nosemgrep: dynamic-urllib-use-detected -- https enforced above
            return 200 <= response.status < 300, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, type(exc).__name__


def _send_hq_inbox(title: str, body: str, severity: str, dedupe_key: str) -> tuple[bool, str]:
    """Drop a note where Sweet Jones will find it."""
    inbox = get_settings().workspace_path / "hq" / "INBOX"
    try:
        inbox.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in dedupe_key)[:48]
        path = inbox / f"blanco-os-{stamp}-{safe}.md"
        path.write_text(
            f"# {EMOJI.get(severity, 'ℹ️')} {title}\n\n"
            f"> From: Blanco OS · severity **{severity}** · {datetime.now(timezone.utc).isoformat(timespec='seconds')}\n\n"
            f"{body}\n\n"
            f"---\nDeck: http://localhost:8800 · dedupe key `{dedupe_key}`\n",
            encoding="utf-8",
        )
        return True, path.name
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc.strerror}"


# --- public API -------------------------------------------------------------


def send(
    conn: sqlite3.Connection,
    title: str,
    body: str = "",
    severity: str = "info",
    dedupe_key: str = "",
    force: bool = False,
) -> dict[str, str]:
    """Fan out to every enabled channel. Returns {channel: detail}."""
    settings = get_settings()
    results: dict[str, str] = {}
    if not settings.notify_enabled:
        return {"disabled": "BLANCO_OS_NOTIFY_ENABLED is false"}

    # `force` means someone asked for this explicitly (a test, a brief push), so
    # it bypasses both the severity floor and the dedupe window.
    if not force and SEVERITY_RANK.get(severity, 0) < SEVERITY_RANK.get(
        settings.notify_min_severity, 0
    ):
        return {"skipped": f"below min severity {settings.notify_min_severity}"}

    key = dedupe_key or title

    announced = False
    for channel, sender in (
        ("hq_inbox", lambda: _send_hq_inbox(title, body, severity, key)),
        ("discord", lambda: _send_discord(title, body, severity)),
    ):
        if channel == "discord" and not settings.discord_webhook_url:
            results[channel] = "not configured"
            continue
        if not force and _already_sent(conn, channel, key):
            results[channel] = "deduped"
            continue
        ok, detail = sender()
        _record(conn, channel, key, title, body, severity, ok, detail)
        results[channel] = detail if ok else f"failed: {detail}"
        announced = True

    # Deliberately not mirrored into the console. Everything announced here
    # started life as a row in the alert tray, which is where it stays — the
    # tray can acknowledge a notice, while the timeline could only let it
    # scroll away between two questions.
    return results


def announce_alerts(conn: sqlite3.Connection, alerts) -> dict[str, str]:
    """Push anything at or above the configured severity. One message each."""
    out: dict[str, str] = {}
    minimum = SEVERITY_RANK.get(get_settings().notify_min_severity, 1)
    for alert in alerts:
        if alert.acknowledged or SEVERITY_RANK.get(alert.severity, 0) < minimum:
            continue
        result = send(
            conn,
            title=alert.title,
            body=alert.detail,
            severity=alert.severity,
            dedupe_key=f"alert-{alert.source}-{alert.title}",
        )
        out[alert.title] = ", ".join(f"{k}={v}" for k, v in result.items())
    return out


def brief_message(brief) -> tuple[str, str]:
    """Render a DailyBrief as (title, body) for a chat message."""
    lines = [f"**{brief.focus.headline}**"]
    if brief.focus.detail:
        lines.append(f"_{brief.focus.detail[:180]}_")
    lines.append("")
    for metric in brief.metrics:
        unit = f" {metric.unit}" if metric.unit and metric.unit != "USD" else ""
        value = f"${metric.value:,.0f}" if metric.unit == "USD" else f"{metric.value:g}{unit}"
        lines.append(f"• {metric.label}: **{value}** — {metric.hint}")
    if brief.now_next:
        lines.append("")
        lines.append("**Next up**")
        for item in brief.now_next[:3]:
            due = f" ({item.due})" if item.due else ""
            lines.append(f"• {item.title}{due}")
    if brief.alerts:
        lines.append("")
        lines.append(f"**{len(brief.alerts)} open alert(s)** — "
                     + ", ".join(a.title for a in brief.alerts[:3]))
    return f"Daily brief — {brief.day}", "\n".join(lines)
