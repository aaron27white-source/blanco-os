"""The agent fleet."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app import schemas
from app.services import agents_service

router = APIRouter(prefix="/api/agents", tags=["agents"])


@router.get("", response_model=schemas.AgentRoster)
def roster():
    return agents_service.roster()


@router.get("/{agent_id}", response_model=schemas.Agent)
def get_agent(agent_id: str):
    agent = agents_service.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="agent not found")
    return agent
