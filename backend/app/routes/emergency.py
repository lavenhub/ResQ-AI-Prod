"""SOS endpoint + incident/notification lifecycle (authenticated, per-user).

Flow (mission Phase 7):
  authenticate -> rate-limit -> idempotency -> require eligible (verified)
  contacts -> validate optional coords -> create persistent incident + durable
  notification jobs in ONE transaction -> return incident id + accurate status.

Recipients are ALWAYS resolved from the authenticated user's stored, verified
contacts — the request body carries no addresses, so this endpoint can never be
used to blast email at arbitrary recipients.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from .. import contact_service, incident_service
from ..auth import get_current_user
from ..config import get_settings
from ..db import get_session
from ..models import (
    IncidentRead,
    NotificationJob,
    NotificationRead,
    NotificationStatus,
    SosRequest,
    SosResponse,
    TriggerSource,
    utcnow,
)
from ..services.notifier import summarize
from ..services.ratelimit import sos_limiter

router = APIRouter(prefix="/api/emergency")


def _sos_response(incident, jobs, created: bool) -> SosResponse:
    return SosResponse(
        incident_id=incident.id,
        created=created,
        status=incident.status,
        location_available=incident.latitude is not None and incident.longitude is not None,
        notifications=[NotificationRead.model_validate(j) for j in jobs],
        notification_summary=summarize(jobs),
    )


@router.post("/sos", response_model=SosResponse)
async def trigger_sos(
    body: SosRequest,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SosResponse:
    settings = get_settings()

    # Rate limit (abuse guard). Idempotent replays of the SAME key are exempted
    # so a legitimate retry of one SOS isn't throttled as a new emergency.
    if not (body.idempotency_key
            and incident_service.get_incident_by_idempotency(session, user_ref, body.idempotency_key)):
        if not sos_limiter.check(
            user_ref, limit=settings.sos_rate_limit, window_seconds=settings.sos_rate_window_seconds
        ):
            raise HTTPException(
                status_code=429,
                detail="Too many SOS requests in a short period. If this is real, call 112/108 now.",
            )

    contacts = contact_service.eligible_contacts(session, user_ref)
    if not contacts:
        raise HTTPException(
            status_code=409,
            detail="No verified emergency contacts to alert. Add a contact and have them "
            "confirm their email, then retry. If this is a real emergency, call 112/108 now.",
        )

    incident, jobs, created = incident_service.create_sos_incident(
        session,
        user_ref=user_ref,
        data=body,
        victim_name=body.victim_name,
        contacts=contacts,
        trigger_source=TriggerSource.APP,
    )
    return _sos_response(incident, jobs, created)


@router.get("/incidents/{incident_id}", response_model=SosResponse)
async def get_incident_status(
    incident_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> SosResponse:
    incident = incident_service.get_incident(session, incident_id)
    if incident is None or incident.user_ref != user_ref:
        raise HTTPException(status_code=404, detail="Incident not found")
    jobs = incident_service.jobs_for_incident(session, incident_id)
    # created=False here — we're reporting an existing incident's live status.
    return _sos_response(incident, jobs, False)


@router.get("/incidents", response_model=list[IncidentRead])
async def list_my_incidents(
    limit: int = 50,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[IncidentRead]:
    from sqlmodel import select

    from ..models import Incident

    limit = max(1, min(limit, 200))
    rows = session.exec(
        select(Incident)
        .where(Incident.user_ref == user_ref)
        .order_by(Incident.created_at.desc())  # type: ignore[attr-defined]
        .limit(limit)
    ).all()
    return [IncidentRead.model_validate(r) for r in rows]


@router.post("/incidents/{incident_id}/cancel", response_model=IncidentRead)
async def cancel_incident(
    incident_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> IncidentRead:
    """Mark an incident cancelled (false alarm).

    Note: this does NOT recall messages already accepted by the carrier — we
    only stop future retries. The UI must communicate that honestly.
    """
    incident = incident_service.cancel_incident(session, user_ref, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident not found")
    # Stop not-yet-sent jobs for this incident (cannot recall already-sent email).
    for job in incident_service.jobs_for_incident(session, incident_id):
        if job.status in (NotificationStatus.QUEUED, NotificationStatus.PENDING):
            job.status = NotificationStatus.CANCELLED
            job.last_error = "incident cancelled before send"
            job.updated_at = utcnow()
            session.add(job)
    session.commit()
    return IncidentRead.model_validate(incident)


@router.post("/notifications/{notification_id}/retry", response_model=NotificationRead)
async def retry_notification(
    notification_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> NotificationRead:
    """Manually re-queue a FAILED or UNKNOWN notification.

    Only the owner may retry, and only non-in-flight, non-delivered jobs are
    eligible — so a retry can never double-send something still PROCESSING or
    already DELIVERED.
    """
    job = session.get(NotificationJob, notification_id)
    if job is None or job.user_ref != user_ref:
        raise HTTPException(status_code=404, detail="Notification not found")
    if job.status not in (NotificationStatus.FAILED, NotificationStatus.UNKNOWN):
        raise HTTPException(
            status_code=409,
            detail=f"Notification is '{job.status.value}' and cannot be retried.",
        )
    job.status = NotificationStatus.QUEUED
    job.attempts = 0
    job.last_error = None
    job.next_attempt_at = utcnow()
    job.locked_at = None
    job.locked_by = None
    job.updated_at = utcnow()
    session.add(job)
    session.commit()
    session.refresh(job)
    return NotificationRead.model_validate(job)
