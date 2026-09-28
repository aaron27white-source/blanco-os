"""Journal + mood.

Backed by `05-journal/diary.json`, the same store The Scribe writes to, so an
entry logged from the dashboard shows up in the Discord-side diary and vice
versa.
"""

from __future__ import annotations

from datetime import date, timedelta

from app import schemas
from app.config import get_settings
from app.vault import read_json, write_json

# Blanco's mood glyphs, worst -> best. Anything unrecognised scores 0.
MOOD_SCORES = {
    "😞": 1, "😢": 1, "😰": 1, "😡": 1,
    "😕": 2, "😐": 3, "🙂": 4, "😊": 4, "😄": 5, "🔥": 5, "💪": 5, "🚀": 5,
}

EMPTY = {"meta": {"version": 1, "name": "5lanxo Diary"}, "entries": {}}


def _load() -> dict:
    data = read_json(get_settings().diary_file, default=None)
    if not isinstance(data, dict) or not isinstance(data.get("entries"), dict):
        return {"meta": dict(EMPTY["meta"]), "entries": {}}
    return data


def _save(data: dict) -> None:
    from datetime import datetime, timezone

    data.setdefault("meta", dict(EMPTY["meta"]))
    data["meta"]["lastUpdated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_json(get_settings().diary_file, data)


def _entry(day: str, raw: dict) -> schemas.JournalEntry:
    tags = raw.get("tags")
    return schemas.JournalEntry(
        date=day,
        mood=str(raw.get("mood") or ""),
        text=str(raw.get("text") or ""),
        tags=[str(t) for t in tags] if isinstance(tags, list) else [],
        created=raw.get("created"),
        updated=raw.get("updated"),
    )


def score(mood: str) -> int:
    for glyph, value in MOOD_SCORES.items():
        if glyph in mood:
            return value
    return 0


def list_entries(limit: int = 60) -> list[schemas.JournalEntry]:
    entries = _load()["entries"]
    days = sorted(entries.keys(), reverse=True)[:limit]
    return [_entry(d, entries[d]) for d in days if isinstance(entries[d], dict)]


def get_entry(day: str) -> schemas.JournalEntry | None:
    raw = _load()["entries"].get(day)
    return _entry(day, raw) if isinstance(raw, dict) else None


def upsert_entry(day: str, payload: schemas.JournalEntryWrite) -> schemas.JournalEntry:
    from datetime import datetime, timezone

    data = _load()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    existing = data["entries"].get(day) if isinstance(data["entries"].get(day), dict) else {}
    data["entries"][day] = {
        "mood": payload.mood,
        "text": payload.text,
        "tags": payload.tags,
        "created": existing.get("created", now),
        "updated": now,
    }
    _save(data)
    return _entry(day, data["entries"][day])


def _streaks(days: list[str]) -> tuple[int, int]:
    """(current, longest) run of consecutive dated entries."""
    if not days:
        return 0, 0
    parsed = sorted({date.fromisoformat(d) for d in days if _is_date(d)}, reverse=True)
    if not parsed:
        return 0, 0

    today = date.today()
    current = 0
    if parsed[0] in (today, today - timedelta(days=1)):
        cursor = parsed[0]
        for day in parsed:
            if day == cursor:
                current += 1
                cursor -= timedelta(days=1)
            elif day < cursor:
                break

    longest, run = 1, 1
    for prev, nxt in zip(parsed, parsed[1:]):
        if prev - nxt == timedelta(days=1):
            run += 1
            longest = max(longest, run)
        else:
            run = 1
    return current, max(longest, current)


def _is_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def overview() -> schemas.JournalOverview:
    data = _load()
    entries = {d: e for d, e in data["entries"].items() if isinstance(e, dict)}
    days = sorted(entries.keys(), reverse=True)
    current, longest = _streaks(days)

    cutoff = (date.today() - timedelta(days=30)).isoformat()
    recent_scores = [score(entries[d].get("mood", "")) for d in days if d >= cutoff]
    scored = [s for s in recent_scores if s]

    series = [
        schemas.MoodPoint(date=d, mood=str(entries[d].get("mood") or ""), score=score(entries[d].get("mood", "")))
        for d in sorted(days)[-90:]
    ]

    return schemas.JournalOverview(
        source_file=str(get_settings().diary_file),
        entry_count=len(entries),
        current_streak=current,
        longest_streak=longest,
        last_entry_date=days[0] if days else None,
        average_score_30d=round(sum(scored) / len(scored), 2) if scored else 0.0,
        series=series,
        recent=[_entry(d, entries[d]) for d in days[:7]],
    )
