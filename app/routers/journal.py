"""Journal and mood — shares diary.json with The Scribe."""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import journal_service, store

router = APIRouter(prefix="/api/journal", tags=["journal"])

ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@router.get("", response_model=schemas.JournalOverview)
def overview():
    return journal_service.overview()


@router.get("/entries", response_model=list[schemas.JournalEntry])
def list_entries(limit: int = Query(60, ge=1, le=365)):
    return journal_service.list_entries(limit=limit)


@router.get("/entries/{day}", response_model=schemas.JournalEntry)
def get_entry(day: str):
    _validate_day(day)
    entry = journal_service.get_entry(day)
    if not entry:
        raise HTTPException(status_code=404, detail="no entry for that day")
    return entry


@router.put("/entries/{day}", response_model=schemas.JournalEntry)
def put_entry(
    day: str,
    payload: schemas.JournalEntryWrite,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_day(day)
    entry = journal_service.upsert_entry(day, payload)
    store.log(db, "journal", "write", day, mood=entry.mood)
    return entry


@router.put("/today", response_model=schemas.JournalEntry)
def put_today(payload: schemas.JournalEntryWrite, db: sqlite3.Connection = Depends(get_db)):
    return put_entry(date.today().isoformat(), payload, db)


def _validate_day(day: str) -> None:
    if not ISO_DAY.match(day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
