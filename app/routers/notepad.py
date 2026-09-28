"""Notepad — the popout widget's scratch notes."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import notepad_service, store

router = APIRouter(prefix="/api/notepad", tags=["notepad"])


@router.get("", response_model=schemas.NotepadOverview, summary="Every note, pinned first")
def list_notes(limit: int = Query(200, ge=1, le=500), db: sqlite3.Connection = Depends(get_db)):
    return notepad_service.list_notes(db, limit=limit)


@router.post("", response_model=schemas.NotepadNote, status_code=201, summary="Start a new note")
def create_note(payload: schemas.NotepadNoteCreate, db: sqlite3.Connection = Depends(get_db)):
    note = notepad_service.create_note(db, payload)
    store.log(db, "notepad", "new note", note.title or note.preview or "untitled")
    return note


@router.get("/{note_id}", response_model=schemas.NotepadNote)
def get_note(note_id: int, db: sqlite3.Connection = Depends(get_db)):
    note = notepad_service.get_note(db, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="no such note")
    return note


@router.patch("/{note_id}", response_model=schemas.NotepadNote, summary="Autosave / rename / pin")
def update_note(
    note_id: int,
    payload: schemas.NotepadNoteUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    try:
        note = notepad_service.update_note(db, note_id, payload)
    except notepad_service.Conflict as clash:
        # The other window saved first. 409 carries the timestamp that won so
        # the editor can re-fetch instead of guessing.
        raise HTTPException(
            status_code=409,
            detail=f"note changed elsewhere at {clash.args[0]} — reload before saving",
        ) from clash
    if not note:
        raise HTTPException(status_code=404, detail="no such note")
    return note


@router.delete("/{note_id}", status_code=204, summary="Delete a note")
def delete_note(note_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not notepad_service.delete_note(db, note_id):
        raise HTTPException(status_code=404, detail="no such note")
    store.log(db, "notepad", "delete note", str(note_id))
