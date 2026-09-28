"""Write-through to the vault's human-readable markdown.

`todo-data.json` is the machine source of truth, but Blanco reads
`todo-list.md` in Obsidian. The workspace already has a 4-hourly cron
(`scripts/sync-todo.py`) that rebuilds the Appointments table from events —
this does the same rebuild immediately after a write, so the note is never
stale by hours.

Two hard rules:

* **Only the Appointments table is touched.** Everything else in
  `todo-list.md` — the Personal / Work / Consultation task tables — is
  hand-curated by Sweet Jones and Blanco. Rewriting it from the JSON would
  destroy content the JSON has never held.
* **Never corrupt on failure.** If the section marker is missing or the file
  is unreadable, the sync reports that and leaves the file exactly as it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from app.config import get_settings
from app.vault import read_json

SECTION = "## 📅 Appointments"
TABLE_HEADER = (
    "| Date | Time | Title | Status | Notes |\n"
    "|------|------|-------|--------|-------|\n"
)


@dataclass
class SyncResult:
    ok: bool
    detail: str
    events_written: int = 0


def _rows(events: list[dict]) -> list[str]:
    out = []
    for e in sorted(events, key=lambda x: (str(x.get("date", "")), str(x.get("time", "")))):
        status = "✅" if e.get("status") == "done" else "📅"
        cells = [
            str(e.get("date") or "—"),
            str(e.get("time") or "—"),
            str(e.get("title") or "—"),
            status,
            str(e.get("reason") or ""),
        ]
        # A stray pipe in a title would break the table.
        out.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    return out


def sync_appointments() -> SyncResult:
    """Rebuild the Appointments table in todo-list.md from todo-data.json."""
    settings = get_settings()
    md_path = settings.todo_markdown_file

    data = read_json(settings.todo_data_file, default=None)
    if not isinstance(data, dict):
        return SyncResult(False, "todo-data.json missing or unreadable")

    events = [e for e in (data.get("events") or []) if isinstance(e, dict)]

    try:
        md = md_path.read_text(encoding="utf-8")
    except OSError:
        return SyncResult(False, f"{md_path.name} not found")

    start = md.find(SECTION)
    if start == -1:
        return SyncResult(False, f"'{SECTION}' section not found — left untouched")

    end = md.find("\n## ", start + len(SECTION))
    if end == -1:
        end = len(md)

    rows = _rows(events)
    body = "\n".join(rows) if rows else "| — | — | _no appointments_ | | |"
    replacement = f"{SECTION}\n\n{TABLE_HEADER}{body}\n\n"
    updated = md[:start] + replacement + md[end:]

    # Keep the "Last updated" stamp honest while we're in here.
    updated = _stamp(updated)

    if updated == md:
        return SyncResult(True, "already up to date", len(rows))

    try:
        md_path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return SyncResult(False, f"could not write {md_path.name}: {exc.strerror}")

    return SyncResult(True, f"Appointments table rebuilt ({len(rows)} events)", len(rows))


def _stamp(md: str) -> str:
    marker = "**Last updated:**"
    idx = md.find(marker)
    if idx == -1:
        return md
    line_end = md.find("\n", idx)
    if line_end == -1:
        line_end = len(md)
    return md[:idx] + f"{marker} {date.today().isoformat()}" + md[line_end:]
