"""Vault knowledge surface: stats, recent notes, plain-text search.

Search is a straight scan. The vault is a few thousand markdown files, which
grep-in-Python handles in well under a second — no index to keep in sync, and
the `.brain-index` chroma DB stays owned by Sweet Jones.
"""

from __future__ import annotations

from pathlib import Path

from app import schemas
from app.config import get_settings
from app.vault import (
    first_heading,
    iter_notes,
    modified_at,
    parse_frontmatter,
    read_text,
    read_text_cached,
)

MAX_SCAN_BYTES = 400_000


def _folder_of(path: Path, vault: Path) -> str:
    try:
        rel = path.relative_to(vault)
    except ValueError:
        return ""
    return rel.parts[0] if len(rel.parts) > 1 else "(root)"


def _note(path: Path, vault: Path, excerpt_chars: int = 180) -> schemas.VaultNote:
    text = read_text(path)
    meta, body = parse_frontmatter(text)
    tags = meta.get("tags", [])
    if isinstance(tags, str):
        tags = [tags]

    excerpt = ""
    for line in body.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", ">", "|", "-", "*", "`")):
            excerpt = stripped[:excerpt_chars]
            break

    try:
        rel = str(path.relative_to(vault))
    except ValueError:
        rel = str(path)

    return schemas.VaultNote(
        title=first_heading(text) or path.stem,
        path=rel,
        folder=_folder_of(path, vault),
        tags=[str(t) for t in tags],
        modified_at=modified_at(path),
        excerpt=excerpt,
    )


def overview(recent_limit: int = 10) -> schemas.KnowledgeOverview:
    settings = get_settings()
    vault = settings.vault_path
    notes = iter_notes(vault)

    by_folder: dict[str, int] = {}
    for path in notes:
        by_folder[_folder_of(path, vault)] = by_folder.get(_folder_of(path, vault), 0) + 1

    topics: list[schemas.FolderStat] = []
    if settings.reference_dir.exists():
        for child in sorted(settings.reference_dir.iterdir()):
            if child.is_dir():
                topics.append(
                    schemas.FolderStat(folder=child.name, note_count=len(list(child.glob("*.md"))))
                )

    return schemas.KnowledgeOverview(
        vault_path=str(vault),
        total_notes=len(notes),
        by_folder=sorted(
            (schemas.FolderStat(folder=f, note_count=c) for f, c in by_folder.items()),
            key=lambda s: s.note_count,
            reverse=True,
        ),
        reference_topics=sorted(topics, key=lambda s: s.note_count, reverse=True),
        recent=[_note(p, vault) for p in notes[:recent_limit]],
    )


def recent(limit: int = 20, folder: str | None = None) -> list[schemas.VaultNote]:
    vault = get_settings().vault_path
    paths = iter_notes(vault)
    if folder:
        paths = [p for p in paths if _folder_of(p, vault) == folder]
    return [_note(p, vault) for p in paths[:limit]]


def search(query: str, limit: int = 25) -> schemas.SearchResults:
    vault = get_settings().vault_path
    needle = query.strip().lower()
    if not needle:
        return schemas.SearchResults(query=query, hit_count=0, hits=[])

    hits: list[schemas.SearchHit] = []
    for path in iter_notes(vault):
        try:
            if path.stat().st_size > MAX_SCAN_BYTES:
                continue
        except OSError:
            continue
        text = read_text_cached(path)
        if needle not in text.lower():
            continue

        title = first_heading(text) or path.stem
        # Title matches outrank body matches; more occurrences outrank fewer.
        occurrences = text.lower().count(needle)
        base = 2.0 if needle in title.lower() else 1.0
        line_no, snippet = _first_match(text, needle)
        try:
            rel = str(path.relative_to(vault))
        except ValueError:
            rel = str(path)
        hits.append(
            schemas.SearchHit(
                title=title,
                path=rel,
                folder=_folder_of(path, vault),
                line=line_no,
                snippet=snippet,
                score=round(base * (1 + min(occurrences, 10) / 10), 3),
            )
        )

    hits.sort(key=lambda h: h.score, reverse=True)
    return schemas.SearchResults(query=query, hit_count=len(hits), hits=hits[:limit])


def _first_match(text: str, needle: str) -> tuple[int, str]:
    for i, line in enumerate(text.splitlines(), start=1):
        low = line.lower()
        if needle in low:
            col = low.index(needle)
            start = max(0, col - 60)
            return i, line[start : start + 200].strip()
    return 1, ""


def read_note(rel_path: str) -> tuple[schemas.VaultNote, str] | None:
    """Full text of one note. Refuses anything resolving outside the vault."""
    vault = get_settings().vault_path.resolve()
    target = (vault / rel_path).resolve()
    if not str(target).startswith(str(vault)) or not target.is_file():
        return None
    return _note(target, vault), read_text(target)
