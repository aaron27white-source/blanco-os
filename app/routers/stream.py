"""Server-sent events, so the UI can feel alive without polling every widget.

Two event types:
  pulse    — a small status heartbeat (alerts, health, agents, unread bridge)
  activity — one event per new row in activity_log
"""

from __future__ import annotations

import asyncio
import json
import sqlite3

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from app.deps import get_db
from app.services import agents_service, bridge_service, store, systems_service

router = APIRouter(prefix="/api", tags=["stream"])


def _pulse(conn: sqlite3.Connection) -> dict:
    agents = agents_service.roster()
    return {
        "open_alerts": len(store.list_alerts(conn)),
        "bridge_unread": bridge_service.unread_count(conn),
        "systems_health": systems_service.health().overall,
        "agents_online": agents.online,
        "agents_total": agents.total,
    }


async def _events(conn: sqlite3.Connection, interval: float, max_events: int | None):
    last_activity_id = 0
    row = conn.execute("SELECT MAX(id) AS m FROM activity_log").fetchone()
    last_activity_id = row["m"] or 0
    sent = 0

    while max_events is None or sent < max_events:
        # Anything new in the log since the last tick.
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE id > ? ORDER BY id", (last_activity_id,)
        ).fetchall()
        for r in rows:
            last_activity_id = r["id"]
            payload = {
                "id": r["id"],
                "module": r["module"],
                "verb": r["verb"],
                "subject": r["subject"],
                "meta": json.loads(r["meta_json"] or "{}"),
                "created_at": r["created_at"],
            }
            yield f"event: activity\ndata: {json.dumps(payload)}\n\n"

        # _pulse is blocking (sqlite + local probes). Running it inline on the
        # event loop stalled every other request for the length of a probe —
        # a 3ms endpoint measured 3s whenever it landed on a tick.
        pulse = await asyncio.to_thread(_pulse, conn)
        yield f"event: pulse\ndata: {json.dumps(pulse)}\n\n"
        sent += 1
        if max_events is not None and sent >= max_events:
            break
        await asyncio.sleep(interval)


@router.get("/stream", summary="SSE feed: pulse + activity")
async def stream(
    interval: float = Query(5.0, ge=1.0, le=60.0),
    max_events: int | None = Query(None, ge=1, description="Stop after N pulses (testing)"),
    db: sqlite3.Connection = Depends(get_db),
):
    return StreamingResponse(
        _events(db, interval, max_events),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
