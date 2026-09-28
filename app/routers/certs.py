"""Certification track parsed from the vault roadmap."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from app import schemas
from app.deps import get_db
from app.services import certs_service, store

router = APIRouter(prefix="/api/certs", tags=["certs"])


@router.get("", response_model=schemas.CertTrack)
def get_track(db: sqlite3.Connection = Depends(get_db)):
    return certs_service.track(db)


@router.get("/{slug}", response_model=schemas.Cert)
def get_cert(slug: str, db: sqlite3.Connection = Depends(get_db)):
    cert = next((c for c in certs_service.track(db).certs if c.slug == slug), None)
    if not cert:
        raise HTTPException(status_code=404, detail="cert not found")
    return cert


@router.patch("/{slug}", response_model=schemas.Cert)
def update_cert(
    slug: str,
    payload: schemas.CertProgressUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    cert = certs_service.update_progress(db, slug, payload)
    if not cert:
        raise HTTPException(status_code=404, detail="cert not found in the roadmap")
    store.log(db, "certs", "progress", cert.name, status=cert.status, percent=cert.percent)
    return cert
