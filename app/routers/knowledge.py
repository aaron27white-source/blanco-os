"""Vault knowledge: stats, recent notes, search, single-note read."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app import schemas
from app.services import knowledge_service

router = APIRouter(prefix="/api/knowledge", tags=["knowledge"])


class NoteDetail(BaseModel):
    note: schemas.VaultNote
    content: str


@router.get("", response_model=schemas.KnowledgeOverview)
def overview(recent: int = Query(10, ge=1, le=50)):
    return knowledge_service.overview(recent_limit=recent)


@router.get("/recent", response_model=list[schemas.VaultNote])
def recent(limit: int = Query(20, ge=1, le=200), folder: str | None = None):
    return knowledge_service.recent(limit=limit, folder=folder)


@router.get("/search", response_model=schemas.SearchResults)
def search(q: str = Query(..., min_length=2), limit: int = Query(25, ge=1, le=100)):
    return knowledge_service.search(q, limit=limit)


@router.get("/note", response_model=NoteDetail, summary="Read one note by vault-relative path")
def note(path: str = Query(..., description="Path relative to the vault root")):
    found = knowledge_service.read_note(path)
    if not found:
        raise HTTPException(status_code=404, detail="note not found")
    meta, content = found
    return NoteDetail(note=meta, content=content)
