"""Infrastructure health, cron schedule, disks."""

from __future__ import annotations

from fastapi import APIRouter

from app import schemas
from app.services import systems_service

router = APIRouter(prefix="/api/systems", tags=["systems"])


@router.get("", response_model=schemas.SystemsHealth)
def health():
    return systems_service.health()


@router.get("/services", response_model=list[schemas.ServiceStatus])
def services():
    return systems_service.services()


@router.get("/cron", response_model=list[schemas.CronEntry])
def cron():
    return systems_service.read_crontab()


@router.get("/disks", response_model=list[schemas.DiskUsage])
def disks():
    return systems_service.disks()
