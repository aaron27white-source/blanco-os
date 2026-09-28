"""Notepad — free-form scratch notes for the popout widget.

Server-side on purpose. The widget is reachable from every view, from a
detached browser window, and from the phone's PWA; localStorage would give
each of those its own private, silently diverging copy. One table, one truth.

Writes are last-write-wins unless the caller passes the `updated_at` it last
saw, in which case a concurrent edit from the other window is a 409 rather
than a silent overwrite.
"""

from __future__ import annotations

import sqlite3

from app import schemas

MAX_BODY = 200_000     # ~100 pages of text; a paste bigger than this is a mistake
MAX_TITLE = 200
PREVIEW_CHARS = 90


class Conflict(Exception):
    """The note changed underneath the editor that is trying to save it."""


def _note(row: sqlite3.Row) -> schemas.NotepadNote:
    body = row["body"] or ""
    return schemas.NotepadNote(
        id=row["id"],
        title=row["title"],
        body=body,
        pinned=bool(row["pinned"]),
        color=row["color"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        preview=_preview(body),
        chars=len(body),
    )


def _preview(body: str) -> str:
    """First line with anything on it, trimmed to fit the list rail."""
    for line in body.splitlines():
        stripped = line.strip().lstrip("#-*> ").strip()
        if stripped:
            return stripped[:PREVIEW_CHARS]
    return ""


def _clean(title: str, body: str) -> tuple[str, str]:
    return title.strip()[:MAX_TITLE], body[:MAX_BODY]


# --- reads ------------------------------------------------------------------


def list_notes(conn: sqlite3.Connection, limit: int = 200) -> schemas.NotepadOverview:
    rows = conn.execute(
        """SELECT * FROM notepad_notes
            ORDER BY pinned DESC, updated_at DESC, id DESC
            LIMIT ?""",
        (limit,),
    ).fetchall()
    total = conn.execute("SELECT COUNT(*) AS n FROM notepad_notes").fetchone()["n"]
    return schemas.NotepadOverview(notes=[_note(r) for r in rows], count=total)


def get_note(conn: sqlite3.Connection, note_id: int) -> schemas.NotepadNote | None:
    row = conn.execute("SELECT * FROM notepad_notes WHERE id = ?", (note_id,)).fetchone()
    return _note(row) if row else None


# --- writes -----------------------------------------------------------------


def create_note(conn: sqlite3.Connection, payload: schemas.NotepadNoteCreate) -> schemas.NotepadNote:
    title, body = _clean(payload.title, payload.body)
    cur = conn.execute(
        "INSERT INTO notepad_notes (title, body, pinned, color) VALUES (?, ?, ?, ?)",
        (title, body, int(payload.pinned), payload.color),
    )
    conn.commit()
    return get_note(conn, int(cur.lastrowid))


def update_note(
    conn: sqlite3.Connection, note_id: int, payload: schemas.NotepadNoteUpdate
) -> schemas.NotepadNote | None:
    row = conn.execute("SELECT * FROM notepad_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    if payload.base_updated_at and payload.base_updated_at != row["updated_at"]:
        raise Conflict(row["updated_at"])

    title = row["title"] if payload.title is None else payload.title
    body = row["body"] if payload.body is None else payload.body
    title, body = _clean(title, body)
    pinned = row["pinned"] if payload.pinned is None else int(payload.pinned)
    color = row["color"] if payload.color is None else payload.color

    conn.execute(
        """UPDATE notepad_notes
              SET title = ?, body = ?, pinned = ?, color = ?,
                  updated_at = strftime('%Y-%m-%d %H:%M:%f', 'now')
            WHERE id = ?""",
        (title, body, pinned, color, note_id),
    )
    conn.commit()
    return get_note(conn, note_id)


def delete_note(conn: sqlite3.Connection, note_id: int) -> bool:
    cur = conn.execute("DELETE FROM notepad_notes WHERE id = ?", (note_id,))
    conn.commit()
    return cur.rowcount > 0
