"""The console — the three commanders, their sub-agents, and the threads."""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app import schemas
from app.db import connect
from app.deps import get_db
from app.services import chat_service, store

router = APIRouter(prefix="/api/chat", tags=["chat"])

# How long a reader blocks waiting for the next event before sending a
# keep-alive. Readers are woken the moment an event is published, so this is
# only the idle ceiling — not the latency of a delta, which is now zero ticks
# rather than up to one.
STREAM_WAIT_SECONDS = 15.0


@router.get("", response_model=list[schemas.ChatMessage],
            summary="The conversation — one session's, or the whole console")
def history(
    limit: int = Query(60, ge=1, le=300),
    session_id: int | None = Query(None, description="Omit for every session"),
    db: sqlite3.Connection = Depends(get_db),
):
    return chat_service.history(db, limit=limit, session_id=session_id)


@router.post("", response_model=schemas.ChatExchange, status_code=202,
             summary="Send a message. Returns immediately; stream the reply id")
def send(payload: schemas.ChatSend, db: sqlite3.Connection = Depends(get_db)):
    try:
        exchange = chat_service.send(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    store.log(db, "chat", f"ask {exchange.message.engine}:{exchange.message.agent}",
              payload.message[:80])
    return exchange


@router.get("/engines", response_model=list[schemas.ChatEngineInfo],
            summary="The three commanders and whether each is installed here")
def engines():
    return chat_service.known_engines()


@router.get("/agents", response_model=list[schemas.ChatAgent],
            summary="Sub-agents — one commander's roster, or all three")
def agents(engine: schemas.ChatEngine | None = Query(None)):
    return chat_service.known_agents(engine)


@router.get("/sessions", response_model=list[schemas.ChatSessionInfo],
            summary="Threads you can switch between, scoped to one commander")
def sessions(
    engine: schemas.ChatEngine | None = Query(None),
    db: sqlite3.Connection = Depends(get_db),
):
    # Selecting a commander for the first time should land in a usable thread
    # rather than an empty tab strip.
    if engine:
        chat_service.ensure_session(db, engine)
    return chat_service.list_sessions(db, engine)


@router.post("/sessions", response_model=schemas.ChatSessionInfo, status_code=201,
             summary="Open a new thread on one commander")
def create_session(payload: schemas.ChatSessionCreate, db: sqlite3.Connection = Depends(get_db)):
    return chat_service.create_session(db, payload.label, payload.engine, payload.agent)


@router.patch("/sessions/{session_id}", response_model=schemas.ChatSessionInfo,
              summary="Rename a thread, point it at another sub-agent, or start it fresh")
def update_session(session_id: int, payload: schemas.ChatSessionUpdate,
                   db: sqlite3.Connection = Depends(get_db)):
    try:
        return chat_service.update_session(
            db, session_id, payload.label, payload.agent, payload.forget
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/sessions/{session_id}", response_model=schemas.Ack,
               summary="Close a thread and its messages")
def delete_session(session_id: int, db: sqlite3.Connection = Depends(get_db)):
    try:
        chat_service.delete_session(db, session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return schemas.Ack(message="thread closed")


@router.get("/{message_id}/stream", summary="SSE: watch one turn happen, live")
async def stream_reply(
    request: Request,
    message_id: int,
    after: int = Query(0, ge=0, description="Resume: last event id already seen"),
    db: sqlite3.Connection = Depends(get_db),
):
    """The console's live view of one turn.

    Replaces a single growing string with the commander's whole working:

    * ``delta`` / ``replace`` — the answer as it is written
    * ``activity``            — tool calls, thinking, status
    * ``usage``               — tokens and dollars, once reported
    * ``done``                — exactly one, carrying the finished row

    Every event carries an SSE id, and a reconnecting browser sends the last
    one it saw back (as `Last-Event-ID`, or `?after=`), so a reload in the
    middle of a seven-minute turn resumes rather than stranding the bubble.
    """
    message = chat_service.get(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")

    resume_from = after
    header = request.headers.get("last-event-id")
    if header and header.isdigit():
        resume_from = max(resume_from, int(header))

    async def events():
        seen = resume_from
        # The generator owns its connection instead of borrowing the request's.
        #
        # `db` belongs to whichever thread resolved the dependency, but every
        # read below runs on an arbitrary threadpool thread via to_thread —
        # which is exactly the cross-thread sharing deps.py opens by saying is
        # unsafe. It surfaced as `sqlite3.InterfaceError: bad parameter or
        # other API misuse` mid-stream, killing the live view of a turn: the
        # deck stopped receiving deltas and the bubble sat pending until the
        # ceiling. Only one to_thread call is ever in flight here, so a single
        # handle of our own is used serially and is safe.
        stream_db = await asyncio.to_thread(connect)
        try:
            # A turn that finished while the client was away has no buffer left
            # to replay; the row is the answer, so send it and close.
            if chat_service.turns.get(message_id) is None:
                finished = await asyncio.to_thread(chat_service.get, stream_db, message_id)
                if finished and finished.status != "pending":
                    yield f"event: done\ndata: {finished.model_dump_json()}\n\n"
                    return

            deadline = asyncio.get_event_loop().time() + chat_service.turn_timeout() + 30
            while asyncio.get_event_loop().time() < deadline:
                if await request.is_disconnected():
                    return
                batch, finished = await asyncio.to_thread(
                    chat_service.turns.since, message_id, seen, STREAM_WAIT_SECONDS
                )
                for event in batch:
                    seen = event.seq
                    yield f"id: {event.seq}\nevent: {event.kind}\ndata: {json.dumps(event.data)}\n\n"

                if finished:
                    # The worker writes the row before closing the buffer, so
                    # this is normally already true. The short wait covers the
                    # gap between the two rather than spinning on it — without
                    # it a finished buffer over a still-pending row turns this
                    # loop into a busy wait for the length of the ceiling.
                    for _ in range(25):
                        row = await asyncio.to_thread(chat_service.get, stream_db, message_id)
                        if row and row.status != "pending":
                            yield f"event: done\ndata: {row.model_dump_json()}\n\n"
                            return
                        await asyncio.sleep(0.2)
                    yield f"event: done\ndata: {json.dumps({'id': message_id, 'status': 'pending'})}\n\n"
                    return

                if not batch:
                    # A comment keeps the connection warm through a long silence
                    # without appearing to the browser as an event.
                    yield ": keep-alive\n\n"

            yield f"event: done\ndata: {json.dumps({'id': message_id, 'status': 'pending'})}\n\n"
        finally:
            # Runs on every exit, including the browser navigating away, which
            # closes the generator with GeneratorExit rather than returning.
            stream_db.close()

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no",
                 "Connection": "keep-alive"},
    )


@router.post("/{message_id}/cancel", response_model=schemas.ChatMessage,
             summary="Stop a running turn, keeping whatever it already wrote")
def cancel_reply(message_id: int, db: sqlite3.Connection = Depends(get_db)):
    """What Esc does in a terminal.

    A turn used to be unstoppable: a wedged commander held the Send button
    disabled for the full ceiling with no way to take the box back. Whatever
    the agent had written by the time it was stopped is kept as the reply.
    """
    if not chat_service.cancel(db, message_id):
        raise HTTPException(status_code=409, detail="that turn is not running")
    # The worker owns the row; give it a moment to record what it had.
    for _ in range(40):
        message = chat_service.get(db, message_id)
        if message and message.status != "pending":
            return message
        time.sleep(0.1)
    return chat_service.get(db, message_id)


@router.get("/pending/all", response_model=list[schemas.ChatMessage],
            summary="Turns still running — what to re-attach to after a reload")
def pending(db: sqlite3.Connection = Depends(get_db)):
    return chat_service.pending_replies(db)


@router.get("/{message_id}", response_model=schemas.ChatMessage,
            summary="Poll one message — the fallback when SSE is unavailable")
def get_message(message_id: int, db: sqlite3.Connection = Depends(get_db)):
    message = chat_service.get(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    return message


@router.delete("", response_model=schemas.Ack,
               summary="Clear a thread's history, or the whole console")
def clear(
    session_id: int | None = Query(None, description="Omit to clear every thread"),
    db: sqlite3.Connection = Depends(get_db),
):
    return schemas.Ack(message=f"{chat_service.clear(db, session_id)} messages cleared")
