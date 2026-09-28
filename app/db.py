"""SQLite connection + forward-only migration runner.

Same pattern as the it-parts-system backend: numbered .sql files applied in
order, current version tracked in `schema_version`.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from app.config import get_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_settings().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # The service holds this connection open for its whole lifetime, so without
    # a size limit the WAL only ever grows: it reached 3MB against an 86KB
    # database. Checkpoint every ~1MB and truncate the file back down after,
    # instead of letting SQLite's 4MB default accumulate.
    conn.execute("PRAGMA wal_autocheckpoint = 256")
    conn.execute("PRAGMA journal_size_limit = 1048576")
    # Cron jobs (backup, checkpoint) touch the same file; wait rather than
    # failing instantly on a write lock.
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return row["v"] or 0


def migrate(conn: sqlite3.Connection) -> int:
    """Apply every migration newer than the recorded version. Returns new version."""
    version = current_version(conn)
    for path in migration_files():
        number = int(path.name[:3])
        if number <= version:
            continue
        conn.executescript(path.read_text())
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (number,))
        conn.commit()
        version = number
    return version


def bootstrap(db_path: Path | None = None) -> sqlite3.Connection:
    conn = connect(db_path)
    migrate(conn)
    _fail_orphaned_replies(conn)
    return conn


def _fail_orphaned_replies(conn: sqlite3.Connection) -> None:
    """Close out replies whose worker died with the last process.

    A pending row is filled in by a daemon thread, and no thread survives a
    restart — so anything still pending at startup is waiting on a worker that
    no longer exists. Left alone the bubble spins forever: one had been
    "thinking" for three weeks. Marking them failed is the honest reading.
    """
    conn.execute(
        """UPDATE chat_messages
              SET status = 'failed',
                  error = 'the turn was interrupted by a restart'
            WHERE status = 'pending'"""
    )
    conn.commit()
