"""Job search — Personal → Job Search: applications and resumes on file."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app import schemas
from app.deps import get_db
from app.services import job_search_service, store

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("", response_model=schemas.JobSearchOverview,
            summary="Every application, status counts, and the resumes in the vault")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return job_search_service.overview(db)


@router.post("", response_model=schemas.JobApplication, status_code=201, summary="Add an application")
def add(payload: schemas.JobApplicationCreate, db: sqlite3.Connection = Depends(get_db)):
    row = job_search_service.add_application(db, payload)
    store.log(db, "jobs", "add application", f"{row.company} — {row.role}".strip(" —"))
    return row


@router.get("/{app_id}", response_model=schemas.JobApplication)
def get(app_id: int, db: sqlite3.Connection = Depends(get_db)):
    row = job_search_service.get_application(db, app_id)
    if not row:
        raise HTTPException(status_code=404, detail="no such application")
    return row


@router.patch("/{app_id}", response_model=schemas.JobApplication, summary="Move or edit an application")
def update(app_id: int, payload: schemas.JobApplicationUpdate, db: sqlite3.Connection = Depends(get_db)):
    row = job_search_service.update_application(db, app_id, payload)
    if not row:
        raise HTTPException(status_code=404, detail="no such application")
    store.log(db, "jobs", "update application", row.company, status=row.status)
    return row


@router.delete("/{app_id}", status_code=204, summary="Delete an application")
def delete(app_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not job_search_service.delete_application(db, app_id):
        raise HTTPException(status_code=404, detail="no such application")
    store.log(db, "jobs", "delete application", str(app_id))
