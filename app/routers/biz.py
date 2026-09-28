"""Your Business — divisions, their ventures, and the deal pipeline."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app import schemas
from app.deps import get_db
from app.services import freight_service, biz_email_service, biz_service

router = APIRouter(prefix="/api/biz", tags=["biz"])


@router.get("", response_model=schemas.BizOverview,
            summary="The whole work OS in one call")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return biz_service.overview(db)


@router.get("/email", response_model=schemas.EmailMonitorHealth,
            summary="Is the inbox monitor alive, and what has it learned?")
def email_monitor(db: sqlite3.Connection = Depends(get_db)):
    """Liveness for the Your Business inbox monitor.

    The alerts themselves arrive as bridge messages; this answers the question
    those cannot — whether the worker is still running. Silence from a dead
    monitor is indistinguishable from a quiet inbox.
    """
    return biz_email_service.health()


# --- freight broker ---------------------------------------------------------
@router.get("/freight", response_model=schemas.FreightTracker,
            summary="The Freight Broker page: roadmap, log and daily numbers")
def freight(db: sqlite3.Connection = Depends(get_db)):
    return freight_service.tracker(db)


@router.put("/freight/steps/{step_id}", response_model=schemas.FreightTracker,
            summary="Check a roadmap step off, or uncheck it")
def freight_step(step_id: str, done: bool = Query(True), db: sqlite3.Connection = Depends(get_db)):
    if not freight_service.set_step(db, step_id, done):
        raise HTTPException(404, f"no roadmap step {step_id!r}")
    return freight_service.tracker(db)


@router.post("/freight/log", response_model=schemas.FreightLogEntry, status_code=201,
             summary="Log something you did")
def freight_log_add(payload: schemas.FreightLogCreate, db: sqlite3.Connection = Depends(get_db)):
    return freight_service.add_log(db, payload)


@router.delete("/freight/log/{entry_id}", status_code=204)
def freight_log_delete(entry_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not freight_service.delete_log(db, entry_id):
        raise HTTPException(404, "no such log entry")


@router.put("/freight/daily/{day}", response_model=schemas.FreightDaily,
            summary="Set one day's numbers (the daily dashboard feed)")
def freight_daily(day: str = Path(pattern=r"^\d{4}-\d{2}-\d{2}$"),
                  payload: schemas.FreightDailyUpsert = ..., db: sqlite3.Connection = Depends(get_db)):
    return freight_service.upsert_daily(db, day, payload)


@router.delete("/freight/daily/{day}", status_code=204)
def freight_daily_delete(day: str, db: sqlite3.Connection = Depends(get_db)):
    if not freight_service.delete_daily(db, day):
        raise HTTPException(404, "no numbers for that day")


# --- deals ------------------------------------------------------------------
# Declared before /divisions/{division_id} so that nothing under /deals is
# swallowed by a path parameter.


@router.get("/deals", response_model=list[schemas.Deal])
def list_deals(
    division_id: str | None = None,
    stage: schemas.DealStage | None = None,
    open_only: bool = False,
    limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return biz_service.list_deals(
        db, division_id=division_id, stage=stage, open_only=open_only, limit=limit
    )


@router.post("/deals", response_model=schemas.Deal, status_code=201)
def add_deal(payload: schemas.DealCreate, db: sqlite3.Connection = Depends(get_db)):
    deal = biz_service.add_deal(db, payload)
    if not deal:
        raise HTTPException(status_code=404, detail="division not found")
    return deal


@router.get("/deals/{deal_id}", response_model=schemas.Deal)
def get_deal(deal_id: int, db: sqlite3.Connection = Depends(get_db)):
    deal = biz_service.get_deal(db, deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="deal not found")
    return deal


@router.patch("/deals/{deal_id}", response_model=schemas.Deal,
              summary="Move a deal along, or change what it is worth")
def update_deal(
    deal_id: int,
    payload: schemas.DealUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    deal = biz_service.update_deal(db, deal_id, payload)
    if not deal:
        raise HTTPException(status_code=404, detail="deal not found")
    return deal


@router.delete("/deals/{deal_id}", response_model=schemas.Ack)
def delete_deal(deal_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not biz_service.delete_deal(db, deal_id):
        raise HTTPException(status_code=404, detail="deal not found")
    return schemas.Ack(message="deleted")


# --- annotations ------------------------------------------------------------
#
# The register of who Blanco is signed up with and what is still open with
# them. Separate from the deal pipeline on purpose — see migration 020.


@router.get("/annotations", response_model=list[schemas.Annotation],
            summary="Companies Blanco is signed up with, and the state of each")
def list_annotations(
    division_id: str | None = None,
    status: schemas.AnnotationStatus | None = None,
    kind: schemas.AnnotationKind | None = None,
    open_only: bool = Query(False, description="Only active, needs_finish or blocked"),
    search: str | None = Query(None, description="Matches company, domain or project"),
    limit: int = Query(500, ge=1, le=2000),
    db: sqlite3.Connection = Depends(get_db),
):
    return biz_service.list_annotations(
        db, division_id=division_id, status=status, kind=kind,
        open_only=open_only, search=search, limit=limit,
    )


@router.post("/annotations", response_model=schemas.Annotation, status_code=201,
             summary="Register one company")
def add_annotation(
    payload: schemas.AnnotationCreate, db: sqlite3.Connection = Depends(get_db)
):
    row = biz_service.add_annotation(db, payload)
    if not row:
        raise HTTPException(status_code=404, detail="division not found")
    return row


@router.post("/annotations/import", response_model=schemas.AnnotationImportResult,
             summary="Bulk upsert — what Hermes points the mail sweep at")
def import_annotations(
    payloads: list[schemas.AnnotationCreate],
    db: sqlite3.Connection = Depends(get_db),
):
    """Idempotent: re-running the same sweep reports `unchanged`, not duplicates.

    Fields Blanco has filled in himself (project, status, next_action, notes,
    division) are never overwritten by a sweep — only filled when still blank.
    """
    return biz_service.import_annotations(db, payloads)


@router.get("/annotations/{annotation_id}", response_model=schemas.Annotation)
def get_annotation(annotation_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = biz_service.get_annotation(db, annotation_id)
    if not row:
        raise HTTPException(status_code=404, detail="annotation not found")
    return row


@router.patch("/annotations/{annotation_id}", response_model=schemas.Annotation,
              summary="Update the project or mark it finished")
def update_annotation(
    annotation_id: int,
    payload: schemas.AnnotationUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    row = biz_service.update_annotation(db, annotation_id, payload)
    if not row:
        raise HTTPException(status_code=404, detail="annotation or division not found")
    return row


@router.delete("/annotations/{annotation_id}", response_model=schemas.Ack)
def delete_annotation(annotation_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not biz_service.delete_annotation(db, annotation_id):
        raise HTTPException(status_code=404, detail="annotation not found")
    return schemas.Ack(message="deleted")


# --- divisions --------------------------------------------------------------


@router.get("/divisions", response_model=list[schemas.Division])
def list_divisions(db: sqlite3.Connection = Depends(get_db)):
    return biz_service.list_divisions(db)


@router.get("/divisions/{division_id}", response_model=schemas.Division)
def get_division(division_id: str, db: sqlite3.Connection = Depends(get_db)):
    division = biz_service.get_division(db, division_id)
    if not division:
        raise HTTPException(status_code=404, detail="division not found")
    return division


@router.patch("/divisions/{division_id}", response_model=schemas.Division,
              summary="Set the stage, target or next action Blanco decides")
def update_division(
    division_id: str,
    payload: schemas.DivisionUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    division = biz_service.update_division(db, division_id, payload)
    if not division:
        raise HTTPException(status_code=404, detail="division not found")
    return division


@router.put("/divisions/{division_id}/ventures/{venture_id}", response_model=schemas.Division,
            summary="Put a venture under this division")
def attach_venture(
    division_id: str,
    venture_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    if not biz_service.attach_venture(db, division_id, venture_id):
        raise HTTPException(status_code=404, detail="division or venture not found")
    return biz_service.get_division(db, division_id)


@router.delete("/divisions/{division_id}/ventures/{venture_id}", response_model=schemas.Division,
               summary="Unparent a venture — its ledger is untouched")
def detach_venture(
    division_id: str,
    venture_id: str,
    db: sqlite3.Connection = Depends(get_db),
):
    if not biz_service.detach_venture(db, division_id, venture_id):
        raise HTTPException(status_code=404, detail="venture is not under that division")
    return biz_service.get_division(db, division_id)
