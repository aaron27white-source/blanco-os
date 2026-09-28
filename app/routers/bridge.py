"""Your Business → Personal OS bridge inbox."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import bridge_service

router = APIRouter(prefix="/api/bridge", tags=["bridge"])


@router.get("", response_model=list[schemas.BridgeMessage])
def list_messages(
    unread_only: bool = False,
    limit: int = Query(50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return bridge_service.list_messages(db, unread_only=unread_only, limit=limit)


@router.post("", response_model=schemas.BridgeMessage, status_code=201,
             summary="Work OS pushes a notice across the bridge")
def receive(payload: schemas.BridgeMessageCreate, db: sqlite3.Connection = Depends(get_db)):
    return bridge_service.receive(db, payload)


@router.post("/{message_id}/read", response_model=schemas.BridgeMessage)
def mark_read(message_id: int, db: sqlite3.Connection = Depends(get_db)):
    message = bridge_service.mark_read(db, message_id)
    if not message:
        raise HTTPException(status_code=404, detail="message not found")
    return message


@router.post("/read-all", response_model=schemas.Ack)
def mark_all_read(db: sqlite3.Connection = Depends(get_db)):
    count = bridge_service.mark_all_read(db)
    return schemas.Ack(message=f"{count} marked read")
