"""Inbox — categorised through the existing email-categorizer rules,
then corrected by Blanco's own importance overrides.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import email_service, store

router = APIRouter(prefix="/api/email", tags=["email"])


@router.get("", response_model=schemas.Inbox, summary="Search / browse mail, ranked by importance")
def inbox(
    limit: int = Query(25, ge=1, le=50),
    unread_only: bool = True,
    q: str = Query("", description="Gmail search syntax — from:, subject:, newer_than:7d, …"),
    category: str = Query("", description="Filter to one of our categories after classification"),
    db: sqlite3.Connection = Depends(get_db),
):
    return email_service.fetch(
        db, limit=limit, unread_only=unread_only, query=q, category=category
    )


@router.get("/status", response_model=schemas.EmailStatus, summary="Pipeline health, no Gmail call")
def status():
    return email_service.status()


@router.get("/categories", response_model=list[str], summary="Every category the categorizer uses")
def categories():
    return email_service.status().categories


# --- importance corrections -------------------------------------------------


@router.get("/rules", response_model=list[schemas.EmailRule], summary="Your importance overrides")
def list_rules(db: sqlite3.Connection = Depends(get_db)):
    return email_service.list_rules(db)


@router.post("/rules", response_model=schemas.EmailRule, status_code=201,
             summary="Teach the OS that this sender/domain/category is worth more or less")
def upsert_rule(payload: schemas.EmailRuleCreate, db: sqlite3.Connection = Depends(get_db)):
    rule = email_service.upsert_rule(db, payload)
    store.log(
        db, "email", "correct importance", f"{rule.scope}:{rule.match_value}",
        importance=rule.importance, was=rule.original_importance,
    )
    return rule


@router.delete("/rules/{rule_id}", response_model=schemas.Ack)
def delete_rule(rule_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not email_service.delete_rule(db, rule_id):
        raise HTTPException(status_code=404, detail="rule not found")
    store.log(db, "email", "remove correction", str(rule_id))
    return schemas.Ack(message="deleted")


# --- message actions --------------------------------------------------------


@router.post("/{message_id}/read", response_model=schemas.Ack)
def mark_read(message_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not email_service.mark_read(message_id):
        raise HTTPException(status_code=502, detail="could not reach Gmail")
    store.log(db, "email", "mark_read", message_id)
    return schemas.Ack(message="marked read")


@router.post("/refresh", response_model=schemas.Ack, summary="Drop the 90s cache")
def refresh():
    email_service.clear_cache()
    return schemas.Ack(message="cache cleared")
