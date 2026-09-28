"""Read-side adapters for the Obsidian vault.

Everything here is defensive: the vault is a living human-edited thing, so a
missing file or a reshaped table must degrade to empty results, never a 500.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
WIKILINK_RE = re.compile(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]")

# Status glyphs used across Blanco's markdown tables.
STATUS_GLYPHS = {
    "🔴": "not_started",
    "🟡": "in_progress",
    "🟠": "in_progress",
    "🟢": "passed",
    "✅": "passed",
    "❌": "failed",
    "⚪": "not_started",
    "⏸": "dropped",
}


def read_text(path: Path) -> str:
    """File contents, or '' when the file is missing/unreadable."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


@lru_cache(maxsize=2048)
def _read_text_at(path: Path, mtime: float) -> str:
    """Cached read keyed on mtime — a touched file invalidates itself."""
    return read_text(path)


def read_text_cached(path: Path) -> str:
    """For scan-heavy paths (search) where the same files are read repeatedly."""
    try:
        return _read_text_at(path, path.stat().st_mtime)
    except OSError:
        return ""


def read_json(path: Path, default: Any = None) -> Any:
    raw = read_text(path)
    if not raw.strip():
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def write_json(path: Path, payload: Any) -> None:
    """Write JSON to the vault.

    Deliberately a plain write, not write-temp-then-rename: the vault sits on a
    /mnt/c FUSE mount where cross-file rename semantics are not dependable.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def modified_at(path: Path) -> str | None:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Minimal YAML frontmatter reader (scalars + inline [a, b] lists).

    Avoids a PyYAML dependency; the vault only uses simple key/value blocks.
    """
    match = FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    meta: dict[str, Any] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.strip().startswith("#"):
            continue
        key, _, value = line.partition(":")
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key.strip()] = [v.strip().strip("'\"") for v in value[1:-1].split(",") if v.strip()]
        else:
            meta[key.strip()] = value.strip("'\"")
    return meta, text[match.end():]


def first_heading(text: str) -> str:
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return ""


def strip_markdown(value: str) -> str:
    """Flatten a table cell to plain text: bold, links, wikilinks, glyph noise."""
    value = WIKILINK_RE.sub(r"\1", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", value)
    value = re.sub(r"[*_`]+", "", value)
    return value.strip()


def status_from_glyph(value: str) -> str | None:
    for glyph, status in STATUS_GLYPHS.items():
        if glyph in value:
            return status
    return None


@dataclass
class MarkdownTable:
    """One pipe table, tagged with the nearest heading above it."""

    heading: str
    headers: list[str]
    rows: list[dict[str, str]] = field(default_factory=list)


def parse_tables(text: str) -> list[MarkdownTable]:
    """Extract every pipe table in a document, in order."""
    tables: list[MarkdownTable] = []
    heading = ""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("#"):
            heading = line.lstrip("#").strip()
            i += 1
            continue
        if line.startswith("|") and i + 1 < len(lines) and _is_divider(lines[i + 1]):
            headers = _split_row(line)
            table = MarkdownTable(heading=heading, headers=headers)
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_row(lines[i])
                if any(c for c in cells):
                    padded = cells + [""] * (len(headers) - len(cells))
                    table.rows.append(dict(zip(headers, padded)))
                i += 1
            tables.append(table)
            continue
        i += 1
    return tables


def _is_divider(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped.startswith("|") and re.fullmatch(r"[|\s:-]+", stripped))


def _split_row(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


SKIP_DIRS = {
    ".obsidian", ".brain-index", "node_modules", "__pycache__", ".git",
    ".venv", "venv", ".next", "dist", "build", ".pytest_cache", ".claude",
}

# The vault sits on a /mnt/c FUSE mount where directory walks are expensive, so
# a full scan is cached briefly. Every read path goes through here.
_SCAN_TTL_SECONDS = 30.0
# Walking the whole vault crosses the /mnt/c mount and takes seconds. Past the
# TTL the previous scan is served anyway while a fresh one runs behind the
# request, so only a genuinely cold cache ever makes someone wait.
_SCAN_STALE_MAX = 900.0
_scan_cache: dict[Path, tuple[float, list[tuple[Path, float]]]] = {}
_scan_refreshing: set[Path] = set()
_scan_lock = threading.Lock()


def _spawn_scan(root: Path) -> None:
    def run() -> None:
        try:
            _scan(root, _force=True)
        except Exception:  # noqa: BLE001 — a failed rescan leaves the stale entry
            pass
        finally:
            with _scan_lock:
                _scan_refreshing.discard(root)

    threading.Thread(target=run, daemon=True).start()


def _scan(root: Path, _force: bool = False) -> list[tuple[Path, float]]:
    """(path, mtime) for every note under root, newest first. Cached."""
    now = time.monotonic()
    if not _force:
        with _scan_lock:
            cached = _scan_cache.get(root)
            age = now - cached[0] if cached else None
            stale = cached is not None and _SCAN_TTL_SECONDS <= age < _SCAN_STALE_MAX
            refresh = stale and root not in _scan_refreshing
            if refresh:
                _scan_refreshing.add(root)
        if cached is not None and age < _SCAN_TTL_SECONDS:
            return cached[1]
        if stale:
            if refresh:
                _spawn_scan(root)
            return cached[1]

    found: list[tuple[Path, float]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk never descends into the skipped trees —
        # rglob cannot do this, and node_modules alone costs minutes here.
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if not name.endswith(".md"):
                continue
            path = Path(dirpath) / name
            try:
                found.append((path, path.stat().st_mtime))
            except OSError:
                continue

    found.sort(key=lambda pair: pair[1], reverse=True)
    with _scan_lock:
        # time.monotonic() again: the walk itself can take seconds, and the
        # entry should age from when it finished, not when it started.
        _scan_cache[root] = (time.monotonic(), found)
    return found


def clear_scan_cache() -> None:
    _scan_cache.clear()


def iter_notes(root: Path, limit: int | None = None) -> list[Path]:
    """Markdown files under `root`, newest first, skipping machine noise."""
    if not root.exists():
        return []
    paths = [p for p, _ in _scan(root)]
    return paths[:limit] if limit else paths
