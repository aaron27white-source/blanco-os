"""What is playing on this PC, across every app."""

from __future__ import annotations

from fastapi import APIRouter

from app import schemas
from app.services import nowplaying_service

router = APIRouter(prefix="/api/nowplaying", tags=["nowplaying"])


@router.get("", response_model=schemas.NowPlaying,
            summary="What is playing — the front session, plus every player Windows sees")
def current():
    return nowplaying_service.read()


@router.post("/control", response_model=schemas.NowPlayingResult,
             summary="Play, pause, skip, stop or seek — on one named player or the front one")
def control(payload: schemas.NowPlayingControl):
    # No 404 for "nothing is playing": the deck polls this and an absent session
    # is an ordinary state, reported in the body rather than as an error.
    return nowplaying_service.control(
        payload.action, app_id=payload.app_id, position_secs=payload.position_secs
    )
