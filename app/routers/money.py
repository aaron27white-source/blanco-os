"""Ventures, revenue events, and the money overview."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import ai_spend_service, money_service

router = APIRouter(prefix="/api/money", tags=["money"])


@router.get("", response_model=schemas.MoneyOverview, summary="Month-to-date across every stream")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return money_service.overview(db)


# --- AI stack spend ---------------------------------------------------------
# Declared before the /{venture_id} routes on the ventures router would be an
# issue, but these live under /api/money, which has no path parameters.


@router.get("/ai-stack", response_model=schemas.AiSpendOverview,
            summary="What the AI stack costs per month")
def ai_stack(db: sqlite3.Connection = Depends(get_db)):
    return ai_spend_service.overview(db)


@router.post("/ai-stack", response_model=schemas.AiService, status_code=201,
             summary="Track another service")
def add_ai_service(payload: schemas.AiServiceCreate, db: sqlite3.Connection = Depends(get_db)):
    service = ai_spend_service.add_service(db, payload)
    if not service:
        raise HTTPException(status_code=409, detail="a service with that name is already tracked")
    return service


@router.patch("/ai-stack/{service_id}", response_model=schemas.AiService,
              summary="Set what it actually costs")
def update_ai_service(
    service_id: int,
    payload: schemas.AiServiceUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    service = ai_spend_service.update_service(db, service_id, payload)
    if not service:
        raise HTTPException(status_code=404, detail="service not found")
    return service


@router.delete("/ai-stack/{service_id}", response_model=schemas.Ack)
def delete_ai_service(service_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not ai_spend_service.delete_service(db, service_id):
        raise HTTPException(status_code=404, detail="service not found")
    return schemas.Ack(message="deleted")


ventures_router = APIRouter(prefix="/api/ventures", tags=["money"])


@ventures_router.get("", response_model=list[schemas.Venture])
def list_ventures(stage: schemas.Stage | None = None, db: sqlite3.Connection = Depends(get_db)):
    return money_service.list_ventures(db, stage=stage)


@ventures_router.get("/{venture_id}", response_model=schemas.Venture)
def get_venture(venture_id: str, db: sqlite3.Connection = Depends(get_db)):
    venture = money_service.get_venture(db, venture_id)
    if not venture:
        raise HTTPException(status_code=404, detail="venture not found")
    return venture


@ventures_router.patch("/{venture_id}", response_model=schemas.Venture)
def update_venture(
    venture_id: str,
    payload: schemas.VentureUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    venture = money_service.update_venture(db, venture_id, payload)
    if not venture:
        raise HTTPException(status_code=404, detail="venture not found")
    return venture


@ventures_router.get("/{venture_id}/events", response_model=list[schemas.VentureEvent])
def list_events(
    venture_id: str,
    limit: int = Query(50, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    if not money_service.get_venture(db, venture_id):
        raise HTTPException(status_code=404, detail="venture not found")
    return money_service.list_events(db, venture_id=venture_id, limit=limit)


@ventures_router.post("/{venture_id}/events", response_model=schemas.VentureEvent, status_code=201)
def add_event(
    venture_id: str,
    payload: schemas.VentureEventCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    event = money_service.add_event(db, venture_id, payload)
    if not event:
        raise HTTPException(status_code=404, detail="venture not found")
    return event


@ventures_router.delete("/{venture_id}/events/{event_id}", response_model=schemas.Ack)
def delete_event(venture_id: str, event_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not money_service.delete_event(db, venture_id, event_id):
        raise HTTPException(status_code=404, detail="event not found for that venture")
    return schemas.Ack(message="deleted")
