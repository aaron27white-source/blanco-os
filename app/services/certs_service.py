"""Certification track.

The roadmap itself is a human-edited markdown file full of pipe tables
(`Cert-Roadmap-Tracker.md`). We parse it for structure — phase, name, cost,
duration, glyph status — then layer OS-side progress on top, so ticking a cert
forward in the UI never has to rewrite Blanco's markdown.
"""

from __future__ import annotations

import re
import sqlite3

from app import schemas
from app.config import get_settings
from app.vault import first_heading, modified_at, parse_tables, read_text, status_from_glyph, strip_markdown

STATUS_ORDER = ["not_started", "in_progress", "passed", "failed", "dropped"]
PERCENT_BY_STATUS = {"not_started": 0, "in_progress": 50, "passed": 100, "failed": 0, "dropped": 0}

BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def slugify(name: str) -> str:
    # "CompTIA A+" and "CompTIA A" must not collide.
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("+", " plus ")).strip("-")
    return slug or "cert"


def cert_name(cell: str) -> str:
    """The cert itself, not the provider note beside it.

    Blanco writes rows like `**CompTIA A+** (Workforce program — FREE)`: the bold run
    is the credential, the parenthetical is how he plans to pay for it.
    """
    match = BOLD_RE.search(cell)
    return strip_markdown(match.group(1) if match else cell)


def _column(row: dict[str, str], *candidates: str) -> str:
    """Pull a cell by fuzzy header match — table headers drift between phases."""
    for header, value in row.items():
        low = header.lower()
        if any(c in low for c in candidates):
            return value
    return ""


def parse_roadmap() -> list[schemas.Cert]:
    text = read_text(get_settings().cert_roadmap_file)
    if not text:
        return []

    certs: list[schemas.Cert] = []
    seen: set[str] = set()
    for table in parse_tables(text):
        headers = " ".join(h.lower() for h in table.headers)
        if "cert" not in headers:
            continue
        for row in table.rows:
            name = cert_name(_column(row, "cert", "name", "course"))
            if not name or name.startswith("-"):
                continue
            slug = slugify(name)
            if slug in seen:
                continue
            seen.add(slug)
            status_cell = _column(row, "status")
            certs.append(
                schemas.Cert(
                    slug=slug,
                    name=name,
                    phase=table.heading or "Roadmap",
                    cost=strip_markdown(_column(row, "cost", "price")),
                    duration=strip_markdown(_column(row, "time", "duration", "length")),
                    status=status_from_glyph(status_cell) or "not_started",
                    percent=0,
                )
            )
    for cert in certs:
        cert.percent = PERCENT_BY_STATUS.get(cert.status, 0)
    return certs


def _overrides(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {r["cert_slug"]: r for r in conn.execute("SELECT * FROM cert_progress").fetchall()}


def track(conn: sqlite3.Connection) -> schemas.CertTrack:
    settings = get_settings()
    certs = parse_roadmap()
    overrides = _overrides(conn)

    for cert in certs:
        row = overrides.get(cert.slug)
        if not row:
            continue
        cert.status = row["status"]
        cert.percent = row["percent"] or PERCENT_BY_STATUS.get(row["status"], 0)
        cert.started_on = row["started_on"]
        cert.finished_on = row["finished_on"]
        cert.note = row["note"]
        cert.overridden = True

    counts = {status: sum(1 for c in certs if c.status == status) for status in STATUS_ORDER}
    counts["total"] = len(certs)
    percent = round(sum(c.percent for c in certs) / len(certs)) if certs else 0

    text = read_text(settings.cert_roadmap_file)
    return schemas.CertTrack(
        title=first_heading(text) or "Certification Roadmap",
        source_file=str(settings.cert_roadmap_file),
        updated_at=modified_at(settings.cert_roadmap_file),
        percent_complete=percent,
        counts=counts,
        certs=certs,
    )


def update_progress(
    conn: sqlite3.Connection, slug: str, payload: schemas.CertProgressUpdate
) -> schemas.Cert | None:
    known = {c.slug: c for c in parse_roadmap()}
    if slug not in known:
        return None

    existing = conn.execute(
        "SELECT * FROM cert_progress WHERE cert_slug = ?", (slug,)
    ).fetchone()
    base = known[slug]
    status = payload.status or (existing["status"] if existing else base.status)
    percent = payload.percent
    if percent is None:
        percent = existing["percent"] if existing else PERCENT_BY_STATUS.get(status, 0)
        if payload.status is not None:
            percent = PERCENT_BY_STATUS.get(status, percent)

    conn.execute(
        """INSERT INTO cert_progress (cert_slug, status, percent, started_on, finished_on, note, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(cert_slug) DO UPDATE SET
             status      = excluded.status,
             percent     = excluded.percent,
             started_on  = COALESCE(excluded.started_on, cert_progress.started_on),
             finished_on = COALESCE(excluded.finished_on, cert_progress.finished_on),
             note        = excluded.note,
             updated_at  = excluded.updated_at""",
        (
            slug,
            status,
            percent,
            payload.started_on,
            payload.finished_on,
            payload.note if payload.note is not None else (existing["note"] if existing else ""),
        ),
    )
    conn.commit()
    return next((c for c in track(conn).certs if c.slug == slug), None)
