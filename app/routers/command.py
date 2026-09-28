"""Command Deck — the home screen's data source."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import command_service, history_service, notify, store

router = APIRouter(prefix="/api/command", tags=["command"])


@router.get("/brief", response_model=schemas.DailyBrief, summary="Everything the home screen needs")
def get_brief(
    sweep: bool = Query(True, description="Re-derive machine alerts before responding"),
    db: sqlite3.Connection = Depends(get_db),
):
    return command_service.brief(db, run_sweep=sweep)


@router.get("/focus", response_model=schemas.Focus)
def get_focus(db: sqlite3.Connection = Depends(get_db)):
    return store.get_focus(db)


@router.put("/focus", response_model=schemas.Focus)
def put_focus(payload: schemas.FocusUpdate, db: sqlite3.Connection = Depends(get_db)):
    return store.set_focus(db, payload)


@router.get("/alerts", response_model=list[schemas.Alert])
def list_alerts(
    include_acked: bool = False,
    db: sqlite3.Connection = Depends(get_db),
):
    return store.list_alerts(db, include_acked=include_acked)


@router.post("/alerts", response_model=schemas.Alert, status_code=201)
def create_alert(payload: schemas.AlertCreate, db: sqlite3.Connection = Depends(get_db)):
    return store.raise_alert(db, payload)


@router.post("/alerts/{alert_id}/ack", response_model=schemas.Alert)
def ack_alert(alert_id: int, db: sqlite3.Connection = Depends(get_db)):
    alert = store.ack_alert(db, alert_id)
    if not alert:
        raise HTTPException(status_code=404, detail="alert not found")
    return alert


@router.post("/sweep", response_model=list[schemas.Alert], summary="Force an alert re-scan")
def run_sweep(db: sqlite3.Connection = Depends(get_db)):
    return command_service.sweep(db)


@router.get("/activity", response_model=list[schemas.ActivityItem])
def activity(limit: int = Query(50, ge=1, le=500), db: sqlite3.Connection = Depends(get_db)):
    return store.recent_activity(db, limit=limit)


# --- notifications ----------------------------------------------------------


@router.post("/notify/test", response_model=schemas.NotifyResult,
             summary="Send a test notification through every enabled channel")
def notify_test(payload: schemas.NotifyTest, db: sqlite3.Connection = Depends(get_db)):
    return schemas.NotifyResult(sent=notify.send(
        db, payload.title, payload.body, payload.severity,
        dedupe_key=f"test-{payload.title}", force=True,
    ))


@router.post("/notify/brief", response_model=schemas.NotifyResult,
             summary="Push the daily brief out (for the morning cron)")
def notify_brief(db: sqlite3.Connection = Depends(get_db)):
    daily = command_service.brief(db)
    title, body = notify.brief_message(daily)
    return schemas.NotifyResult(sent=notify.send(
        db, title, body, "info", dedupe_key=f"brief-{daily.day}", force=True,
    ))


# --- metric history ---------------------------------------------------------


@router.post("/history/snapshot", response_model=schemas.Ack,
             summary="Record today's metrics so trends can be computed")
def snapshot(db: sqlite3.Connection = Depends(get_db)):
    daily = command_service.brief(db, run_sweep=False)
    written = history_service.snapshot(db, daily.metrics)
    store.log(db, "command", "snapshot", f"{written} metrics")
    return schemas.Ack(message=f"{written} metrics recorded")


@router.get("/history/{metric_key}", response_model=schemas.MetricSeries,
            summary="Daily values for one metric")
def history(metric_key: str, days: int = Query(30, ge=1, le=365),
            db: sqlite3.Connection = Depends(get_db)):
    return schemas.MetricSeries(
        metric_key=metric_key, points=history_service.series(db, metric_key, days)
    )


@router.get("/history", response_model=list[str], summary="Metrics with recorded history")
def history_keys(db: sqlite3.Connection = Depends(get_db)):
    return history_service.tracked_keys(db)
