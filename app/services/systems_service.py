"""Infrastructure health.

Everything here is a cheap local probe — TCP connect, pgrep, stat, statvfs —
so the dashboard can poll it every few seconds without cost. Nothing leaves
the machine.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import schemas
from app.config import get_settings
from app.services.store import now_iso

CRON_FIELD_RE = re.compile(r"^(@\w+|(?:\S+\s+){4}\S+)\s+(.*)$")

HUMAN_CRON = {
    "0 */2 * * *": "every 2 hours",
    "0 */4 * * *": "every 4 hours",
    "*/5 * * * *": "every 5 minutes",
    "0 9 * * 1": "Mondays 9:00 AM",
    "@reboot": "on boot",
}


def port_open(host: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def process_running(pattern: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", pattern], capture_output=True, text=True, timeout=3
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return False


def _newest_mtime(path: Path) -> float | None:
    """Newest mtime at or under `path`.

    A directory's own mtime only changes when its *direct* children change, so
    statting a folder misses a note written into one of its subfolders — which
    once had the OS calling a job days stale when it had run that morning.
    Always walk.
    """
    try:
        if path.is_file():
            return path.stat().st_mtime
        if not path.is_dir():
            return None
    except OSError:
        return None

    newest = None
    for dirpath, dirnames, filenames in os.walk(path):
        dirnames[:] = [d for d in dirnames if not d.startswith(".")]
        for name in filenames:
            try:
                mtime = (Path(dirpath) / name).stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def _file_freshness(path: Path, max_age_hours: float) -> tuple[str, str]:
    if not path.exists():
        return "down", f"{path} missing"
    newest = _newest_mtime(path)
    if newest is None:
        return "stale", f"{path.name} is empty"
    age = datetime.now(timezone.utc) - datetime.fromtimestamp(newest, tz=timezone.utc)
    hours = age.total_seconds() / 3600
    if hours > max_age_hours:
        return "stale", f"last wrote {hours:.1f}h ago (expected < {max_age_hours:g}h)"
    return "up", f"last wrote {hours:.1f}h ago"


# Hermes runs its own scheduler, and jobs moved there leave no trace in the
# user crontab. Its state file is the authority on when a job last ran.
HERMES_CRON_JOBS = Path.home() / ".hermes" / "cron" / "jobs.json"


def hermes_jobs() -> list[dict]:
    """Every job registered with Hermes cron, or [] if Hermes isn't installed."""
    try:
        raw = json.loads(HERMES_CRON_JOBS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    jobs = raw.get("jobs") if isinstance(raw, dict) else raw
    return [j for j in jobs if isinstance(j, dict)] if isinstance(jobs, list) else []


def find_hermes_job(*needles: str) -> dict | None:
    """First job whose name or script mentions one of `needles`."""
    for job in hermes_jobs():
        haystack = f"{job.get('name', '')} {job.get('script', '')}".lower()
        if any(n.lower() in haystack for n in needles):
            return job
    return None


def _job_interval_hours(job: dict) -> float | None:
    schedule = job.get("schedule") or {}
    minutes = schedule.get("minutes")
    return minutes / 60 if isinstance(minutes, (int, float)) and minutes > 0 else None


def hermes_job_freshness(job: dict) -> tuple[str, str]:
    """Judge a Hermes job by its own last run, not by a log file's mtime.

    A job whose script only writes to its log when something goes wrong looks
    dead the moment it starts behaving — which is exactly backwards. Hermes
    records the run itself, so ask Hermes.
    """
    if not job.get("enabled", True) or job.get("state") == "paused":
        return "stale", f"paused — {job.get('paused_reason') or 'disabled in Hermes cron'}"
    if job.get("last_status") in {"error", "failed"}:
        return "down", f"last run failed — {(job.get('last_error') or 'no detail')[:120]}"

    last = job.get("last_run_at")
    if not last:
        # Registered but never fired: not broken, just not proven yet.
        nxt = str(job.get("next_run_at") or "")[:16].replace("T", " ")
        return "up", f"scheduled ({job.get('schedule_display', 'unknown')}) — first run {nxt or 'pending'}"

    try:
        ran = datetime.fromisoformat(last)
    except ValueError:
        return "unknown", f"unreadable last run: {last!r}"
    if ran.tzinfo is None:
        ran = ran.replace(tzinfo=timezone.utc)
    hours = (datetime.now(timezone.utc) - ran).total_seconds() / 3600

    every = _job_interval_hours(job)
    # One skipped run is a blip; two means the ticker is not firing it.
    grace = (every * 2.5) if every else 6.0
    if hours > grace:
        return "stale", f"last ran {hours:.1f}h ago ({job.get('schedule_display', 'unknown')})"
    return "up", f"last ran {hours:.1f}h ago, ok ({job.get('schedule_display', 'unknown')})"


def _crontab_text() -> str:
    """The user's crontab, however it can be had.

    `crontab -l` is the supported way, but the binary is setgid and the service
    runs with NoNewPrivileges — so under systemd it comes back empty while it
    works fine in a terminal. The spool file is readable by its owner when the
    process is in the crontab group, so fall through to it rather than showing
    Blanco an empty schedule and letting him believe nothing is scheduled.
    """
    try:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout
    except (OSError, subprocess.SubprocessError):
        pass
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or Path.home().name
    for spool in (Path("/var/spool/cron/crontabs") / user, Path("/var/spool/cron") / user):
        try:
            return spool.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
    return ""


def read_crontab() -> list[schemas.CronEntry]:
    entries: list[schemas.CronEntry] = []
    pending_comment = ""
    for line in _crontab_text().splitlines():
        stripped = line.strip()
        if not stripped:
            pending_comment = ""
            continue
        if stripped.startswith("#"):
            pending_comment = stripped.lstrip("# ").strip()
            continue
        match = CRON_FIELD_RE.match(stripped)
        if not match:
            continue
        entries.append(
            schemas.CronEntry(
                schedule=match.group(1).strip(),
                command=match.group(2).strip(),
                comment=pending_comment,
            )
        )
        pending_comment = ""
    return entries


def hermes_cron_entries() -> list[schemas.CronEntry]:
    """Hermes jobs rendered as cron lines, so the System page shows one
    schedule list rather than hiding half the machine's timetable."""
    out: list[schemas.CronEntry] = []
    for job in hermes_jobs():
        state = job.get("state") or ("enabled" if job.get("enabled", True) else "disabled")
        last = str(job.get("last_run_at") or "")[:16].replace("T", " ")
        out.append(
            schemas.CronEntry(
                schedule=job.get("schedule_display") or "hermes",
                command=f"hermes cron · {job.get('script') or job.get('name', '')}",
                comment=" · ".join(
                    p for p in (
                        job.get("name", ""),
                        f"last run {last} ({job.get('last_status') or 'unknown'})" if last else "never run",
                        state,
                    ) if p
                ),
            )
        )
    return out


def disks() -> list[schemas.DiskUsage]:
    out: list[schemas.DiskUsage] = []
    for mount in ("/", "/mnt/c"):
        try:
            usage = shutil.disk_usage(mount)
        except OSError:
            continue
        gb = 1024**3
        out.append(
            schemas.DiskUsage(
                mount=mount,
                total_gb=round(usage.total / gb, 1),
                used_gb=round(usage.used / gb, 1),
                free_gb=round(usage.free / gb, 1),
                percent_used=round(usage.used / usage.total * 100, 1) if usage.total else 0.0,
            )
        )
    return out


def uptime() -> str:
    try:
        seconds = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        return ""
    delta = timedelta(seconds=int(seconds))
    days, rem = divmod(delta.total_seconds(), 86400)
    hours, rem = divmod(rem, 3600)
    return f"{int(days)}d {int(hours)}h {int(rem // 60)}m"


def _probe_services() -> list[schemas.ServiceStatus]:
    settings = get_settings()
    checked = now_iso()
    probes = settings.probes_enabled
    out: list[schemas.ServiceStatus] = []

    def add(
        id_: str, name: str, kind: str, status: str, target: str,
        detail: str = "", optional: bool = False,
    ) -> None:
        out.append(
            schemas.ServiceStatus(
                id=id_, name=name, kind=kind, status=status, target=target,
                detail=detail, last_checked=checked, optional=optional,
            )
        )

    # OpenClaw / Sweet Jones
    host, _, port = settings.openclaw_url.removeprefix("http://").partition(":")
    oc_port = int(port or 80)
    oc_up = port_open(host, oc_port, settings.probe_timeout_seconds) if probes else False
    add("openclaw", "OpenClaw — Sweet Jones 🍯", "http", "up" if oc_up else ("down" if probes else "unknown"),
        settings.openclaw_url, "primary orchestrator")

    # Legacy todo server — Blanco OS supersedes it, so it being down is expected.
    todo_up = port_open("127.0.0.1", 8799, settings.probe_timeout_seconds) if probes else False
    add("todo-server", "Todo server (legacy)", "http", "up" if todo_up else ("down" if probes else "unknown"),
        "http://127.0.0.1:8799", "superseded by Blanco OS /api/tasks", optional=True)

    # Dispatch bot — process + 5-minute watchdog
    bot_up = process_running("dispatch-bot") if probes else False
    add("dispatch-bot-bot", "Dispatch freight bot 🦅", "process", "up" if bot_up else ("down" if probes else "unknown"),
        "dispatch_bot/start_bot.sh", "watchdog re-launches every 5 min")

    # Email categorizer — Hermes owns the schedule now, so ask Hermes. Its shim
    # only appends to the categorizer log on stderr, which meant a clean run
    # left the log untouched and a healthy job read as stale forever.
    categorizer_log = Path("/home/you/workspace/email-categorizer/email-categorizer.log")
    job = find_hermes_job("email organizer", "email-digest")
    if job:
        status, detail = hermes_job_freshness(job)
        target = f"hermes cron · {job.get('id', '')}"
    else:
        status, detail = _file_freshness(categorizer_log, max_age_hours=3)
        target = str(categorizer_log)
    add("email-categorizer", "Email categorizer 📧", "cron", status, target, detail)

    # Todo sync — judged by todo-data.json freshness (runs every 4h)
    status, detail = _file_freshness(settings.todo_data_file, max_age_hours=24 * 7)
    add("todo-sync", "Todo sync (JSON → MD)", "file", status, str(settings.todo_data_file), detail)

    # Vault reachability — the FUSE mount does drop
    vault_ok = settings.vault_path.exists()
    add("vault", "Second-brain vault 🧠", "file", "up" if vault_ok else "down",
        str(settings.vault_path), "Obsidian PARA vault over /mnt/c")

    return out


# A sweep is not cheap: the two freshness probes walk a few hundred vault files
# over the /mnt/c mount, which costs seconds per call. The SSE pulse asks for
# health every 5s and the daily brief asks again, so without a cache the box
# spends most of its time re-statting the same files. Past the TTL the previous
# sweep is served while a fresh one runs behind the request — a plain TTL still
# made one unlucky request per window wait out the whole walk.
_STALE_MAX = 900.0
_services_lock = threading.Lock()
_services_cache: tuple[float, list[schemas.ServiceStatus]] | None = None
_services_refreshing = False


def _spawn_sweep() -> None:
    def run() -> None:
        global _services_cache, _services_refreshing
        try:
            fresh = _probe_services()
            with _services_lock:
                _services_cache = (time.monotonic(), fresh)
        except Exception:  # noqa: BLE001 — a failed sweep leaves the stale entry
            pass
        finally:
            with _services_lock:
                _services_refreshing = False

    threading.Thread(target=run, daemon=True).start()


def services() -> list[schemas.ServiceStatus]:
    global _services_cache, _services_refreshing
    ttl = get_settings().systems_cache_seconds
    now = time.monotonic()
    with _services_lock:
        cached = _services_cache
        age = now - cached[0] if cached else None
        if cached is not None and age < ttl:
            return list(cached[1])
        if cached is not None and age < _STALE_MAX:
            spawn = not _services_refreshing
            if spawn:
                _services_refreshing = True
        else:
            spawn = False
    if cached is not None and age < _STALE_MAX:
        if spawn:
            _spawn_sweep()
        return list(cached[1])

    fresh = _probe_services()
    with _services_lock:
        _services_cache = (time.monotonic(), fresh)
    return list(fresh)


def invalidate_services_cache() -> None:
    """Drop the cached sweep — for tests and for a forced re-probe."""
    global _services_cache, _services_refreshing
    with _services_lock:
        _services_cache = None
        _services_refreshing = False


def health(service_list: list[schemas.ServiceStatus] | None = None) -> schemas.SystemsHealth:
    svc = service_list if service_list is not None else services()
    statuses = {s.status for s in svc if not s.optional}
    if "down" in statuses:
        overall = "red"
    elif "stale" in statuses or "unknown" in statuses:
        overall = "yellow"
    else:
        overall = "green"

    return schemas.SystemsHealth(
        overall=overall,
        checked_at=now_iso(),
        services=svc,
        cron=read_crontab() + hermes_cron_entries(),
        disks=disks(),
        uptime=uptime(),
    )
