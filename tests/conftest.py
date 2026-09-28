"""Test fixtures.

Every test runs against a throwaway vault and a throwaway SQLite file, with
outbound probes disabled — the real second-brain is never touched.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    """A miniature second-brain with the four files the OS reads."""
    root = tmp_path / "second-brain"
    (root / "02-areas" / "agent-ops").mkdir(parents=True)
    (root / "05-journal").mkdir(parents=True)
    (root / "Personal" / "projects" / "certs").mkdir(parents=True)
    (root / "Personal" / "areas" / "finance").mkdir(parents=True)
    (root / "03-resources" / "reference" / "credit").mkdir(parents=True)

    (root / "02-areas" / "todo-data.json").write_text(
        json.dumps(
            {
                "version": 1,
                "last_synced": "2026-07-20",
                "events": [
                    {"id": "ev-1", "title": "Study block", "date": "2099-01-01", "time": "19:00",
                     "agent": True, "reason": "cert pipeline"}
                ],
                "boardTasks": [
                    {"id": "bt-1", "title": "Place first GovDeals bid", "priority": "high",
                     "status": "todo", "dueDate": "2020-01-01", "notes": "", "agent": True,
                     "agentReason": "Search criteria ready."},
                    {"id": "bt-2", "title": "Call ITAD companies", "priority": "medium",
                     "status": "done", "dueDate": "", "notes": ""},
                ],
                "notes": [{"id": "n-1", "text": "vault lives at 02-areas", "date": "2026-07-21"}],
                "activity": [],
            }
        )
    )

    # The human-readable counterpart. Shaped like the real one: hand-curated
    # sections that the sync must never touch, around an Appointments table
    # that it owns.
    (root / "02-areas" / "todo-list.md").write_text(
        """# 📋 Blanco's Todo List

**Last updated:** 2026-07-21

---

## 👤 Personal

| Date Added | Task | Status |
|-----------|------|--------|
| 2026-07-18 | Feed repo-auditor prompt to Claude Code | ⏳ |

## 💼 Work / Northwind Electronics

| Date Added | Task | Status |
|-----------|------|--------|
| 2026-07-19 | Source ITADs in Houston | 🔴 Not Started |

## 📅 Appointments

| Date | Time | Title | Status | Notes |
|------|------|-------|--------|-------|
| 2026-07-23 | 19:00 | Study block | 📅 | seeded |

## 📌 Quick Reference

Nothing here yet.
"""
    )

    (root / "05-journal" / "diary.json").write_text(
        json.dumps(
            {
                "meta": {"version": 1, "name": "5lanxo Diary"},
                "entries": {
                    "2026-07-13": {"mood": "🔥", "text": "Server build plan today.",
                                   "tags": ["server"], "created": "2026-07-13T11:30:00-05:00"},
                    "2026-07-12": {"mood": "😊", "text": "Good day.", "tags": []},
                },
            }
        )
    )

    (root / "Personal" / "projects" / "certs" / "Cert-Roadmap-Tracker.md").write_text(
        """# 🎯 Blanco's AI Engineer Roadmap

### Phase 0 — Get Income

| # | Cert | Est. Cost | Est. Time | Status |
|---|------|-----------|-----------|--------|
| 0 | **CompTIA A+** (free via workforce program) | **$0** | 6-10 weeks | 🔴 Not Started |

### Phase 1a — Python Foundation

| # | Cert | Est. Cost | Est. Time | Status |
|---|------|-----------|-----------|--------|
| 1a | **Google IT Automation with Python** | **$0** | 2-3 months | 🟡 In Progress |
| 1b | **IBM AI Engineering** | **$0** | 4-5 months | 🔴 Not Started |
"""
    )

    (root / "03-resources" / "reference" / "credit" / "2026-07-24_scoring.md").write_text(
        "---\ntags: [credit, reference]\n---\n\n# Credit Scoring Basics\n\nUtilization drives the score.\n"
    )
    return root


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    root.mkdir()

    # Two sub-agent directories, shaped like the real ones, so roster
    # discovery is exercised rather than mocked.
    subagents = root / "subagents"
    (subagents / "circuit").mkdir(parents=True)
    (subagents / "circuit" / "IDENTITY.md").write_text(
        "# Circuit 💻 — Northwind Electronics Sub-Agent\n\n"
        "- **Role:** Electronics business advisor — IT parts flipping, sourcing, pricing\n"
    )
    (subagents / "mic").mkdir(parents=True)
    (subagents / "mic" / "IDENTITY.md").write_text(
        "# Mic 🎤 — Music Sub-Agent\n\n- **Role:** Rap career advisor — music, branding, releases\n"
    )
    # A stray directory with no IDENTITY.md must be ignored.
    (subagents / "notes").mkdir(parents=True)

    (root / "ACTIVE.md").write_text(
        """# ACTIVE.md

## 🔴 Current Focus — Active Now
**🖥️ OptiPlex 3060 → Proxmox Server Build**
- Setting up the OptiPlex as a Proxmox host
- Goal: Sweet Jones 24/7

## Other Active Threads
- Something else
"""
    )
    return root


@pytest.fixture
def client(vault: Path, workspace: Path, tmp_path: Path, monkeypatch):
    """TestClient wired to the temp vault, temp DB, and probes off."""
    monkeypatch.setenv("BLANCO_OS_VAULT_PATH", str(vault))
    monkeypatch.setenv("BLANCO_OS_WORKSPACE_PATH", str(workspace))
    monkeypatch.setenv("BLANCO_OS_DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("BLANCO_OS_PROBES_ENABLED", "false")

    from app.config import get_settings
    from app.deps import reset_db

    get_settings.cache_clear()
    reset_db()

    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client

    reset_db()
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_commander_turns(monkeypatch):
    """A test must never spawn a real turn on any of the three commanders.

    They cost money, take tens of seconds, and are non-deterministic. This is
    not hypothetical — an earlier version of this guard covered only the
    subprocess.run path, and the first test to exercise the streaming worker
    quietly reached the live CLI and passed on a real answer.

    Both spawn paths are covered: `run` for the roster probes (Hermes profiles,
    session lookups) and `Popen` for the turn itself. Anything that wants to
    exercise the worker should monkeypatch it explicitly.
    """
    import subprocess

    from app.services import commanders

    binaries = {c.id for c in commanders.ALL}

    def offending(cmd) -> str:
        first = str(cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd)
        name = first.rsplit("/", 1)[-1]
        return name if name in binaries else ""

    for attr in ("run", "Popen"):
        real = getattr(subprocess, attr)

        def guarded(cmd, *args, _real=real, _attr=attr, **kwargs):
            if (name := offending(cmd)):
                raise AssertionError(
                    f"test tried to run a real {name} turn via {_attr}: {cmd!r}. "
                    "Mock it instead."
                )
            return _real(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, attr, guarded)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Stop a stray .env or exported var from leaking into a test run."""
    for key in list(os.environ):
        if key.startswith("BLANCO_OS_"):
            monkeypatch.delenv(key, raising=False)
