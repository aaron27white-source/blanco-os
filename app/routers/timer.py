"""Work / break session timer."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app import schemas
from app.deps import get_db
from app.services import timer_service

router = APIRouter(prefix="/api/timer", tags=["timer"])


@router.get("", response_model=schemas.TimerOverview, summary="Active session + today's totals")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return timer_service.overview(db)


@router.post("/start", response_model=schemas.FocusSession, status_code=201,
             summary="Start a session, replacing any running one")
def start(payload: schemas.TimerStart, db: sqlite3.Connection = Depends(get_db)):
    return timer_service.start(db, payload)


@router.post("/stop", response_model=schemas.FocusSession, summary="Cancel the running session")
def stop(db: sqlite3.Connection = Depends(get_db)):
    session = timer_service.stop(db, completed=False)
    if not session:
        raise HTTPException(status_code=404, detail="no session running")
    return session


@router.post("/complete", response_model=schemas.FocusSession, summary="Mark it finished early")
def complete(db: sqlite3.Connection = Depends(get_db)):
    session = timer_service.stop(db, completed=True)
    if not session:
        raise HTTPException(status_code=404, detail="no session running")
    return session
