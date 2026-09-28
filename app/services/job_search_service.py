"""Job search — employment applications and the resumes on file.

Applications live in SQLite (migration 023). Resumes are read from the vault's
career folder and never written: the files there are the source of truth, so
the tab lists what is on disk rather than a copy that could drift.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app import schemas
from app.config import get_settings

STATUSES: tuple[str, ...] = ("saved", "applied", "interviewing", "offer", "rejected", "withdrawn")
# Statuses that mean an application actually went out.
SENT = frozenset({"applied", "interviewing", "offer", "rejected"})
EDITABLE = frozenset({"company", "role", "status", "source", "link", "pay",
                      "applied_on", "next_action", "notes"})
UPDATE_SQL = {
    column: f"UPDATE job_applications SET {column} = ?, updated_at = datetime('now') WHERE id = ?"
    for column in EDITABLE
}
CAREER_DIR = ("Personal", "areas", "career")
RESUME_SUFFIXES = {".md", ".pdf", ".html", ".docx"}


def _row(row: sqlite3.Row) -> schemas.JobApplication:
    return schemas.JobApplication(**{k: row[k] for k in schemas.JobApplication.model_fields})


def list_applications(conn: sqlite3.Connection) -> list[schemas.JobApplication]:
    rows = conn.execute(
        "SELECT * FROM job_applications ORDER BY "
        "CASE status WHEN 'offer' THEN 0 WHEN 'interviewing' THEN 1 WHEN 'applied' THEN 2 "
        "WHEN 'saved' THEN 3 ELSE 4 END, updated_at DESC, id DESC"
    ).fetchall()
    return [_row(r) for r in rows]


def get_application(conn: sqlite3.Connection, app_id: int) -> schemas.JobApplication | None:
    row = conn.execute("SELECT * FROM job_applications WHERE id = ?", (app_id,)).fetchone()
    return _row(row) if row else None


def add_application(conn: sqlite3.Connection,
                    payload: schemas.JobApplicationCreate) -> schemas.JobApplication:
    data = payload.model_dump()
    if data["status"] in SENT and not data["applied_on"]:
        data["applied_on"] = date.today().isoformat()
    cur = conn.execute(
        "INSERT INTO job_applications (company, role, status, source, link, pay, applied_on,"
        " next_action, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        tuple(data[k] for k in ("company", "role", "status", "source", "link", "pay",
                                "applied_on", "next_action", "notes")),
    )
    conn.commit()
    return get_application(conn, cur.lastrowid)


def update_application(conn: sqlite3.Connection, app_id: int,
                       payload: schemas.JobApplicationUpdate) -> schemas.JobApplication | None:
    current = get_application(conn, app_id)
    if not current:
        return None
    fields = {k: v for k, v in payload.model_dump(exclude_unset=True).items()
              if k in EDITABLE and v is not None}
    if fields.get("status") in SENT and not current.applied_on and "applied_on" not in fields:
        fields["applied_on"] = date.today().isoformat()
    if fields:
        # One fixed statement per column: no SQL is ever built from input.
        for column, value in fields.items():
            conn.execute(UPDATE_SQL[column], (value, app_id))
        conn.commit()
    return get_application(conn, app_id)


def delete_application(conn: sqlite3.Connection, app_id: int) -> bool:
    cur = conn.execute("DELETE FROM job_applications WHERE id = ?", (app_id,))
    conn.commit()
    return cur.rowcount > 0


def resumes() -> list[schemas.ResumeFile]:
    """Resume files in the vault's career folder, newest first. Top level only:
    no recursive walk over the FUSE mount."""
    folder = get_settings().vault_path.joinpath(*CAREER_DIR)
    if not folder.is_dir():
        return []
    out = []
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() in RESUME_SUFFIXES and "resume" in path.name.lower():
            stamp = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d")
            out.append(schemas.ResumeFile(name=path.name, modified=stamp))
    return sorted(out, key=lambda r: (r.modified, r.name), reverse=True)


def overview(conn: sqlite3.Connection) -> schemas.JobSearchOverview:
    apps = list_applications(conn)
    counts = {s: 0 for s in STATUSES}
    for a in apps:
        counts[a.status] += 1
    return schemas.JobSearchOverview(applications=apps, counts=counts, resumes=resumes())
