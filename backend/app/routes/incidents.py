"""Incident REST API — create / fetch / update / list."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from .. import incident_service
from ..db import get_session
from ..models import IncidentCreate, IncidentRead, IncidentStatus, IncidentUpdate

router = APIRouter(prefix="/api/incidents")


@router.post("", response_model=IncidentRead)
async def create_incident(
    body: IncidentCreate,
    response: Response,
    session: Session = Depends(get_session),
) -> IncidentRead:
    incident, created = incident_service.create_incident(session, body)
    # 201 for a new record, 200 when idempotency returned an existing one.
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return IncidentRead.model_validate(incident)


@router.get("", response_model=list[IncidentRead])
async def list_incidents(
    limit: int = 50,
    status_filter: IncidentStatus | None = None,
    session: Session = Depends(get_session),
) -> list[IncidentRead]:
    limit = max(1, min(limit, 200))
    rows = incident_service.list_incidents(session, limit=limit, status=status_filter)
    return [IncidentRead.model_validate(r) for r in rows]


@router.get("/{incident_id}", response_model=IncidentRead)
async def get_incident(
    incident_id: str,
    session: Session = Depends(get_session),
) -> IncidentRead:
    incident = incident_service.get_incident(session, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentRead.model_validate(incident)


@router.patch("/{incident_id}", response_model=IncidentRead)
async def update_incident(
    incident_id: str,
    body: IncidentUpdate,
    session: Session = Depends(get_session),
) -> IncidentRead:
    incident = incident_service.update_incident(session, incident_id, body)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    return IncidentRead.model_validate(incident)
