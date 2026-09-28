"""Your Business (work OS) → Personal OS notification bridge.

Deliberately one-way, per the Agentic OS blueprint: work pushes notices in,
the personal OS never reaches back to command Your Business's departments.
"""

from __future__ import annotations

import sqlite3

from app import schemas
from app.services import store


def _message(row: sqlite3.Row) -> schemas.BridgeMessage:
    return schemas.BridgeMessage(
        id=row["id"],
        origin=row["origin"],
        kind=row["kind"],
        title=row["title"],
        body=row["body"],
        amount=row["amount"],
        read=bool(row["read"]),
        read_at=row["read_at"],
        received_at=row["received_at"],
    )


def list_messages(conn: sqlite3.Connection, unread_only: bool = False, limit: int = 50):
    sql = "SELECT * FROM bridge_messages"
    if unread_only:
        sql += " WHERE read = 0"
    sql += " ORDER BY received_at DESC, id DESC LIMIT ?"
    return [_message(r) for r in conn.execute(sql, (limit,)).fetchall()]


def unread_count(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT COUNT(*) AS n FROM bridge_messages WHERE read = 0").fetchone()
    return int(row["n"])


def receive(conn: sqlite3.Connection, payload: schemas.BridgeMessageCreate) -> schemas.BridgeMessage:
    cur = conn.execute(
        """INSERT INTO bridge_messages (origin, kind, title, body, amount)
           VALUES (?, ?, ?, ?, ?)""",
        (payload.origin, payload.kind, payload.title, payload.body, payload.amount),
    )
    conn.commit()
    store.log(conn, "bridge", payload.kind, payload.title, origin=payload.origin)

    # Escalations are loud enough to earn a Command Deck alert.
    if payload.kind == "escalation":
        store.raise_alert(
            conn,
            schemas.AlertCreate(
                source="bridge",
                severity="critical",
                title=f"Your Business escalation: {payload.title}",
                detail=payload.body,
                action_label="Open bridge",
                action_href="/bridge",
                dedupe_key=f"bridge-escalation-{cur.lastrowid}",
            ),
        )

    row = conn.execute("SELECT * FROM bridge_messages WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _message(row)


def mark_read(conn: sqlite3.Connection, message_id: int) -> schemas.BridgeMessage | None:
    # `AND read = 0` so re-opening a notice keeps the first read time. The
    # learning loop measures how fast Blanco reacted; letting a second click
    # overwrite that would erase the signal.
    conn.execute(
        "UPDATE bridge_messages SET read = 1, read_at = datetime('now') "
        "WHERE id = ? AND read = 0",
        (message_id,),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM bridge_messages WHERE id = ?", (message_id,)).fetchone()
    return _message(row) if row else None


def mark_all_read(conn: sqlite3.Connection) -> int:
    cur = conn.execute(
        "UPDATE bridge_messages SET read = 1, read_at = datetime('now') WHERE read = 0")
    conn.commit()
    return cur.rowcount
