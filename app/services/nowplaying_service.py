"""What is playing anywhere on this PC.

The deck's own player is a YouTube iframe and can only see its own queue — a
web page cannot inspect another browser tab, and it certainly cannot talk to a
Store app. Windows can do both. Its media transport session manager reports
every app that registers transport controls, so one bridge covers a YouTube tab
in Opera or Chrome, the YourMusic app from the Store, Spotify, and Media Player,
and the same API sends play/pause/skip back to whichever is in front.

This shells out to PowerShell over WSL interop. That is a ~400ms cold start, so
results are cached with the same TTL/stale pattern `email_service` uses — this
sits on the home screen and gets polled.

Everything degrades to `available=False` rather than raising: Blanco OS has to
stay usable on a box with no Windows underneath it.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import threading
import time
from pathlib import Path

from app import schemas

log = logging.getLogger(__name__)

_SCRIPT = Path(__file__).resolve().parent.parent.parent / "scripts" / "nowplaying.ps1"

# WSL only injects the Windows PATH into interactive shells, so a bare
# "powershell.exe" resolves from a terminal and fails under systemd — which is
# how Blanco OS actually runs. Look it up by absolute path first and fall back
# to the PATH lookup for any layout not covered here.
_POWERSHELL_CANDIDATES = (
    "/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    "/mnt/c/Program Files/PowerShell/7/pwsh.exe",
    "powershell.exe",
)


def _powershell() -> str | None:
    for candidate in _POWERSHELL_CANDIDATES:
        if "/" in candidate:
            if Path(candidate).is_file():
                return candidate
        elif shutil.which(candidate):
            return candidate
    return None

# Playback state moves constantly, so the window is short — just long enough to
# collapse the deck's poll and any concurrent request into one subprocess.
_CACHE_TTL = 2.0
_TIMEOUT = 8.0

_lock = threading.Lock()
_cache: tuple[float, schemas.NowPlaying] | None = None

# Friendly names for the app ids Windows reports. Anything unknown falls back to
# the raw id, which is still more use than nothing.
_APP_LABELS = {
    "52971PietroDisc.AppMusic": "YourMusic",
    "Microsoft.ZuneMusic": "Media Player",
    "Spotify.exe": "Spotify",
    "SpotifyAB.SpotifyMusic": "Spotify",
}
_BROWSER_HINTS = ("chrome", "opera", "edge", "firefox", "brave", "vivaldi")


def _label_for(app_id: str) -> str:
    if not app_id:
        return ""
    for known, label in _APP_LABELS.items():
        if app_id.lower().startswith(known.lower()):
            return label
    lowered = app_id.lower()
    for hint in _BROWSER_HINTS:
        if hint in lowered:
            return "Browser"
    # 'Some.Vendor.App_8wekyb3d8bbwe!App' -> 'App'
    return app_id.split("!")[-1].split(".")[-1] or app_id


def _run(action: str, app_id: str = "", position_secs: int = 0) -> dict:
    """Invoke the PowerShell helper. Returns its JSON, or an unavailable dict."""
    if not _SCRIPT.is_file():
        return {"available": False, "error": "nowplaying.ps1 is missing"}
    shell = _powershell()
    if shell is None:
        return {"available": False, "error": "not running under Windows"}

    argv = [shell, "-NoProfile", "-NonInteractive",
            "-ExecutionPolicy", "Bypass", "-File", str(_SCRIPT), "-Action", action]
    # Passed as bound parameters, never interpolated into a command string:
    # an app id is attacker-adjacent data (it comes from whatever app happens
    # to be running) and PowerShell would happily expand a `$(...)` inside one.
    if app_id:
        argv += ["-App", app_id]
    if action == "seek":
        argv += ["-Position", str(max(0, int(position_secs)))]

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=_TIMEOUT)
    except FileNotFoundError:
        # Not on Windows / no WSL interop. Expected, not an error worth logging
        # on every poll.
        return {"available": False, "error": "not running under Windows"}
    except subprocess.TimeoutExpired:
        log.warning("nowplaying.ps1 timed out after %ss", _TIMEOUT)
        return {"available": False, "error": "timed out talking to Windows"}
    except OSError as exc:
        return {"available": False, "error": str(exc)}

    out = (proc.stdout or "").strip()
    if not out:
        return {"available": False,
                "error": (proc.stderr or "no output from nowplaying.ps1").strip()[:200]}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        log.warning("nowplaying.ps1 returned non-JSON: %r", out[:200])
        return {"available": False, "error": "unreadable response"}


def _to_session(raw: dict) -> schemas.MediaSession:
    app_id = str(raw.get("app_id") or "")
    title = str(raw.get("title") or "")
    # A session with no title is a player sitting idle with nothing loaded.
    # Report it as stopped so the deck hides the strip instead of rendering a
    # blank card.
    status = str(raw.get("status") or "stopped")
    if not title and status != "stopped":
        status = "stopped"
    return schemas.MediaSession(
        app_id=app_id,
        app_label=_label_for(app_id),
        title=title,
        artist=str(raw.get("artist") or ""),
        album=str(raw.get("album") or ""),
        status=status,
        position_secs=max(0, int(raw.get("position_secs") or 0)),
        duration_secs=max(0, int(raw.get("duration_secs") or 0)),
        can_seek=bool(raw.get("can_seek")),
        can_next=bool(raw.get("can_next")),
        can_previous=bool(raw.get("can_previous")),
        can_pause=bool(raw.get("can_pause")),
    )


def _to_schema(raw: dict) -> schemas.NowPlaying:
    # Players with nothing loaded are dropped from the list rather than shown
    # as empty rows — a browser keeps its session registered long after the tab
    # stopped playing, and a picker full of blanks is worse than no picker.
    sessions = [s for s in (_to_session(entry) for entry in (raw.get("sessions") or []))
                if s.title]
    return schemas.NowPlaying(
        **_to_session(raw).model_dump(),
        available=bool(raw.get("available")),
        error=str(raw.get("error") or ""),
        thumbnail=str(raw.get("thumbnail") or ""),
        sessions=sessions,
    )


def read(force: bool = False) -> schemas.NowPlaying:
    """Current session. Cached for `_CACHE_TTL` seconds."""
    global _cache
    if not force:
        with _lock:
            hit = _cache
        if hit and (time.monotonic() - hit[0]) < _CACHE_TTL:
            return hit[1]

    result = _to_schema(_run("read"))
    with _lock:
        _cache = (time.monotonic(), result)
    return result


def control(action: str, app_id: str = "", position_secs: int = 0) -> schemas.NowPlayingResult:
    """Drive one player — the named app, or whichever session is in front."""
    global _cache
    raw = _run(action, app_id=app_id, position_secs=position_secs)
    # The state just changed, so the cached read is wrong by definition.
    with _lock:
        _cache = None
    return schemas.NowPlayingResult(
        available=bool(raw.get("available")),
        ok=bool(raw.get("ok")),
        error=str(raw.get("error") or ""),
    )
