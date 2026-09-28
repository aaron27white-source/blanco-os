"""Credit scores, accounts, disputes, and the build plan."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import credit_service

router = APIRouter(prefix="/api/credit", tags=["credit"])


@router.get("", response_model=schemas.CreditOverview, summary="The whole credit tab in one call")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return credit_service.overview(db)


# --- scores -----------------------------------------------------------------


@router.get("/scores", response_model=list[schemas.CreditScore], summary="Score history")
def list_scores(
    bureau: schemas.Bureau | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return credit_service.list_scores(db, bureau=bureau, limit=limit)


@router.get("/scores/latest", response_model=list[schemas.CreditScore],
            summary="Most recent reading per bureau and score type")
def latest_scores(db: sqlite3.Connection = Depends(get_db)):
    return credit_service.latest_scores(db)


@router.post("/scores", response_model=schemas.CreditScore, status_code=201,
             summary="Record a score reading")
def add_score(payload: schemas.CreditScoreCreate, db: sqlite3.Connection = Depends(get_db)):
    return credit_service.add_score(db, payload)


# --- accounts ---------------------------------------------------------------


@router.get("/accounts", response_model=list[schemas.CreditAccount])
def list_accounts(active_only: bool = False, db: sqlite3.Connection = Depends(get_db)):
    return credit_service.list_accounts(db, active_only=active_only)


@router.post("/accounts", response_model=schemas.CreditAccount, status_code=201)
def add_account(payload: schemas.CreditAccountCreate, db: sqlite3.Connection = Depends(get_db)):
    return credit_service.add_account(db, payload)


@router.patch("/accounts/{account_id}", response_model=schemas.CreditAccount)
def update_account(
    account_id: int,
    payload: schemas.CreditAccountUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    account = credit_service.update_account(db, account_id, payload)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return account


# --- disputes ---------------------------------------------------------------


@router.get("/disputes", response_model=list[schemas.CreditDispute])
def list_disputes(
    outcome: schemas.DisputeOutcome | None = None,
    bureau: schemas.Bureau | None = None,
    db: sqlite3.Connection = Depends(get_db),
):
    return credit_service.list_disputes(db, outcome=outcome, bureau=bureau)


@router.get("/disputes/overdue", response_model=list[schemas.CreditDispute],
            summary="Past the 30-day FCRA window with no response")
def overdue_disputes(db: sqlite3.Connection = Depends(get_db)):
    return credit_service.overdue_disputes(db)


@router.post("/disputes", response_model=schemas.CreditDispute, status_code=201)
def add_dispute(payload: schemas.CreditDisputeCreate, db: sqlite3.Connection = Depends(get_db)):
    return credit_service.add_dispute(db, payload)


@router.patch("/disputes/{dispute_id}", response_model=schemas.CreditDispute,
              summary="Record a send date or an outcome")
def update_dispute(
    dispute_id: int,
    payload: schemas.CreditDisputeUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    dispute = credit_service.update_dispute(db, dispute_id, payload)
    if not dispute:
        raise HTTPException(status_code=404, detail="dispute not found")
    return dispute


# --- plan -------------------------------------------------------------------


@router.get("/plan", response_model=list[schemas.CreditPlanStep], summary="Every step of the plan")
def list_plan(phase: str | None = None, db: sqlite3.Connection = Depends(get_db)):
    return credit_service.list_plan(db, phase=phase)


@router.get("/plan/next", response_model=list[schemas.CreditPlanStep],
            summary="Open steps in the earliest unfinished phase")
def next_steps(db: sqlite3.Connection = Depends(get_db)):
    return credit_service.next_steps(db)


@router.post("/plan/{step_id}/done", response_model=schemas.CreditPlanStep)
def complete_step(step_id: int, db: sqlite3.Connection = Depends(get_db)):
    step = credit_service.complete_step(db, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="plan step not found")
    return step


@router.post("/plan/{step_id}/reopen", response_model=schemas.CreditPlanStep)
def reopen_step(step_id: int, db: sqlite3.Connection = Depends(get_db)):
    step = credit_service.reopen_step(db, step_id)
    if not step:
        raise HTTPException(status_code=404, detail="plan step not found")
    return step
