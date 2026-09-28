"""Now Playing bridge.

The PowerShell side can only be exercised on a Windows host with something
actually playing, so these tests stub the subprocess and cover the parts that
break silently: parsing, app labelling, caching, and the degraded paths on a
machine with no Windows underneath.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from app.services import nowplaying_service as np


@pytest.fixture(autouse=True)
def _clear_cache():
    np._cache = None
    yield
    np._cache = None


def _stub(monkeypatch, payload, *, stderr=""):
    """Replace the PowerShell call with a canned response."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        out = payload if isinstance(payload, str) else json.dumps(payload)
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr=stderr)

    monkeypatch.setattr(np.subprocess, "run", fake_run)
    return calls


PLAYING = {
    "available": True, "error": "",
    "app_id": "OperaSoftware.OperaAirWebBrowser.1779978329",
    "title": "The Joe Budden Podcast Episode 951",
    "artist": "Joe Budden TV", "album": "",
    "status": "playing", "position_secs": 132, "duration_secs": 3600,
    "thumbnail": "data:image/png;base64,AAAA",
}


def test_reads_a_browser_session(monkeypatch):
    _stub(monkeypatch, PLAYING)
    now = np.read(force=True)
    assert now.available
    assert now.status == "playing"
    assert now.title == "The Joe Budden Podcast Episode 951"
    assert now.artist == "Joe Budden TV"
    assert now.position_secs == 132
    # A browser tab is the case the whole bridge exists for.
    assert now.app_label == "Browser"


@pytest.mark.parametrize(
    "app_id,expected",
    [
        ("52971PietroDisc.AppMusic_eft9pwkrwnhsy!App", "YourMusic"),
        ("Microsoft.ZuneMusic_8wekyb3d8bbwe!Microsoft.ZuneMusic", "Media Player"),
        ("OperaSoftware.OperaAirWebBrowser.123", "Browser"),
        ("Chrome", "Browser"),
        ("", ""),
    ],
)
def test_app_labels(app_id, expected):
    assert np._label_for(app_id) == expected


def test_untitled_session_reports_stopped(monkeypatch):
    """A player sitting idle with nothing loaded must not render a blank card."""
    _stub(monkeypatch, {**PLAYING, "title": "", "status": "playing"})
    assert np.read(force=True).status == "stopped"


def test_result_is_cached(monkeypatch):
    calls = _stub(monkeypatch, PLAYING)
    np.read(force=True)
    np.read()
    np.read()
    assert len(calls) == 1, "cached reads must not re-spawn PowerShell"


def test_control_invalidates_the_cache(monkeypatch):
    _stub(monkeypatch, PLAYING)
    np.read(force=True)
    _stub(monkeypatch, {"available": True, "ok": True, "action": "play_pause"})
    result = np.control("play_pause")
    assert result.ok
    # Playback state just changed, so the cached read is wrong by definition.
    assert np._cache is None


def test_no_windows_is_not_an_error(monkeypatch):
    """The deck has to stay usable on a box with no Windows underneath."""
    def boom(cmd, **kwargs):
        raise FileNotFoundError("powershell.exe")

    monkeypatch.setattr(np.subprocess, "run", boom)
    monkeypatch.setattr(np, "_powershell", lambda: "powershell.exe")
    now = np.read(force=True)
    assert now.available is False
    assert "Windows" in now.error
    assert now.status == "stopped"


def test_powershell_resolved_by_absolute_path(monkeypatch):
    """PowerShell must be found without relying on PATH.

    WSL only injects the Windows PATH into interactive shells, so a bare
    "powershell.exe" resolves from a terminal and fails under systemd — which
    is how Blanco OS actually runs. This passed in dev and returned "not
    running under Windows" in production.
    """
    # PATH lookup unavailable, absolute path present: must still resolve.
    monkeypatch.setattr(np.shutil, "which", lambda _: None)
    monkeypatch.setattr(np.Path, "is_file", lambda self: True)
    assert np._powershell() == np._POWERSHELL_CANDIDATES[0]
    assert np._powershell().startswith("/"), "must not depend on PATH"


def test_missing_powershell_degrades(monkeypatch):
    """No PowerShell anywhere: report it, never raise."""
    monkeypatch.setattr(np, "_powershell", lambda: None)
    now = np.read(force=True)
    assert now.available is False
    assert "Windows" in now.error
    assert now.status == "stopped"


def test_timeout_degrades(monkeypatch):
    def slow(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, np._TIMEOUT)

    monkeypatch.setattr(np.subprocess, "run", slow)
    assert np.read(force=True).available is False


def test_garbage_output_degrades(monkeypatch):
    _stub(monkeypatch, "not json at all")
    now = np.read(force=True)
    assert now.available is False
    assert now.error


def test_endpoints(client, monkeypatch):
    _stub(monkeypatch, PLAYING)
    np._cache = None
    body = client.get("/api/nowplaying").json()
    assert body["title"] == PLAYING["title"]
    assert body["app_label"] == "Browser"

    _stub(monkeypatch, {"available": True, "ok": True})
    assert client.post("/api/nowplaying/control",
                       json={"action": "next"}).json()["ok"] is True
    # Only the three transport verbs are accepted.
    assert client.post("/api/nowplaying/control",
                       json={"action": "delete_everything"}).status_code == 422


# --- several players at once --------------------------------------------------

TWO_PLAYERS = {
    "available": True, "error": "",
    "app_id": "OperaSoftware.OperaAirWebBrowser.1", "title": "A video",
    "artist": "A channel", "status": "playing",
    "position_secs": 95, "duration_secs": 227,
    "can_seek": True, "can_next": True, "can_previous": True, "can_pause": True,
    "sessions": [
        {"app_id": "OperaSoftware.OperaAirWebBrowser.1", "title": "A video",
         "artist": "A channel", "status": "playing",
         "position_secs": 95, "duration_secs": 227, "can_seek": True},
        {"app_id": "Spotify.exe", "title": "A song", "artist": "An artist",
         "status": "paused", "position_secs": 10, "duration_secs": 200,
         "can_seek": True},
        # A browser keeps its session registered long after the tab went quiet.
        {"app_id": "Chrome", "title": "", "status": "stopped"},
    ],
}


def test_every_player_is_reported_not_just_the_front_one(monkeypatch):
    _stub(monkeypatch, TWO_PLAYERS)
    now = np.read()

    assert now.app_label == "Browser" and now.title == "A video"
    # The idle Chrome session is dropped: a picker full of blanks is worse
    # than no picker.
    assert [s.app_label for s in now.sessions] == ["Browser", "Spotify"]
    assert now.sessions[1].status == "paused"


def test_capabilities_come_from_the_app_itself(monkeypatch):
    _stub(monkeypatch, TWO_PLAYERS)
    now = np.read()
    assert now.can_seek is True and now.can_next is True
    # Absent flags read as "no", so an old bridge greys controls out rather
    # than offering buttons that silently do nothing.
    assert now.sessions[1].can_next is False


def test_control_targets_one_named_player(monkeypatch):
    calls = _stub(monkeypatch, {"available": True, "ok": True})
    np.control("pause", app_id="Spotify.exe")

    argv = calls[0]
    assert argv[argv.index("-Action") + 1] == "pause"
    # Bound parameter, never interpolated — an app id is data from whatever
    # happens to be running, and PowerShell would expand a $(...) inside one.
    assert argv[argv.index("-App") + 1] == "Spotify.exe"


def test_seek_passes_a_clamped_position(monkeypatch):
    calls = _stub(monkeypatch, {"available": True, "ok": True})
    np.control("seek", position_secs=42)
    assert calls[0][calls[0].index("-Position") + 1] == "42"

    np.control("seek", position_secs=-5)
    assert calls[1][calls[1].index("-Position") + 1] == "0"


def test_position_is_only_sent_for_a_seek(monkeypatch):
    calls = _stub(monkeypatch, {"available": True, "ok": True})
    np.control("play_pause", position_secs=42)
    assert "-Position" not in calls[0]


def test_a_player_that_refuses_is_reported_not_raised(monkeypatch):
    _stub(monkeypatch, {"available": True, "ok": False, "error": "no player matching 'x'"})
    result = np.control("next", app_id="x")
    assert result.available is True and result.ok is False
    assert "no player matching" in result.error
