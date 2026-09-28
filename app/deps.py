"""Shared FastAPI dependencies."""

from __future__ import annotations

import sqlite3
import threading

from app import db as db_module
from app.config import Settings, get_settings

# One connection per thread rather than one for the whole process.
#
# FastAPI runs sync endpoints in a threadpool, so a single shared handle had
# concurrent requests interleaving statements on it — which surfaced as
# "bad parameter or other API misuse" and "cannot commit - no transaction is
# active" under nothing more exotic than two views loading at once. SQLite
# handles are not safe to share that way; WAL mode makes each thread's writes
# visible to the others, so per-thread handles cost nothing but fix the race.
_local = threading.local()
_lock = threading.Lock()
_migrated = False
_generation = 0


def init_db() -> sqlite3.Connection:
    """Open (once per thread) and migrate (once per process) the OS database."""
    global _migrated, _generation
    conn = getattr(_local, "conn", None)
    # reset_db() bumps the generation; a stale per-thread handle is dropped.
    if conn is not None and getattr(_local, "generation", None) == _generation:
        return conn

    with _lock:
        if not _migrated:
            boot = db_module.bootstrap()
            _migrated = True
            _local.conn = boot
            _local.generation = _generation
            return boot

    conn = db_module.connect()
    _local.conn = conn
    _local.generation = _generation
    return conn


def get_db() -> sqlite3.Connection:
    return init_db()


def reset_db() -> None:
    """Drop cached handles so the next call re-opens (tests use this)."""
    global _migrated, _generation
    with _lock:
        conn = getattr(_local, "conn", None)
        if conn is not None:
            conn.close()
            _local.conn = None
        _migrated = False
        # Invalidates every other thread's cached handle too.
        _generation += 1


def settings() -> Settings:
    return get_settings()
