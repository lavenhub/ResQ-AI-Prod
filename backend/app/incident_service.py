"""Incident business logic — the ONE validated path for mutating incidents.

Both the REST API and the voice-agent tools go through here, so validation and
idempotency rules are enforced in a single place. The LLM never touches the DB
directly; it can only call these functions with validated arguments.
"""

from __future__ import annotations

from sqlmodel import Session, select

from .models import (
    Incident,
    IncidentCreate,
    IncidentStatus,
    IncidentUpdate,
    NotificationJob,
    SosRequest,
    TriggerSource,
    utcnow,
)


def get_incident(session: Session, incident_id: str) -> Incident | None:
    return session.get(Incident, incident_id)


def get_incident_by_session(session: Session, session_id: str) -> Incident | None:
    return session.exec(
        select(Incident).where(Incident.session_id == session_id)
    ).first()


def list_incidents(
    session: Session,
    *,
    limit: int = 50,
    status: IncidentStatus | None = None,
) -> list[Incident]:
    stmt = select(Incident).order_by(Incident.created_at.desc())  # type: ignore[attr-defined]
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    return list(session.exec(stmt.limit(limit)).all())


def create_incident(session: Session, data: IncidentCreate) -> tuple[Incident, bool]:
    """Create an incident. Returns (incident, created).

    Idempotent on ``session_id``: if an incident already exists for that voice
    session, the existing record is returned (created=False) instead of making a
    duplicate — this prevents retries / re-sent tool calls spawning many
    incidents for one emergency.
    """
    if data.session_id:
        existing = get_incident_by_session(session, data.session_id)
        if existing is not None:
            return existing, False

    incident = Incident(
        # exclude_none keeps non-nullable enum columns (severity) on their
        # defaults when the caller didn't supply them.
        **data.model_dump(exclude_unset=True, exclude_none=True, exclude={"status"}),
    )
    # Default a freshly created incident to GATHERING unless caller forced a status.
    incident.status = data.status or IncidentStatus.GATHERING

    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident, True


def update_incident(
    session: Session, incident_id: str, data: IncidentUpdate
) -> Incident | None:
    incident = session.get(Incident, incident_id)
    if incident is None:
        return None

    # PATCH semantics: apply only fields the caller explicitly set, so partial
    # updates never wipe previously gathered details with nulls.
    changes = data.model_dump(exclude_unset=True)
    for key, value in changes.items():
        # Never null-out the non-nullable enum columns.
        if key in {"severity", "status"} and value is None:
            continue
        setattr(incident, key, value)

    if changes:
        incident.updated_at = utcnow()

    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident


# ── SOS orchestration ─────────────────────────────────────────────────────────
def get_incident_by_idempotency(
    session: Session, user_ref: str, key: str
) -> Incident | None:
    return session.exec(
        select(Incident).where(
            Incident.user_ref == user_ref,
            Incident.idempotency_key == key,
        )
    ).first()


def jobs_for_incident(session: Session, incident_id: str) -> list[NotificationJob]:
    return list(
        session.exec(
            select(NotificationJob)
            .where(NotificationJob.incident_id == incident_id)
            .order_by(NotificationJob.created_at)  # type: ignore[arg-type]
        ).all()
    )


def create_sos_incident(
    session: Session,
    *,
    user_ref: str,
    data: SosRequest,
    victim_name: str | None,
    contacts,
    trigger_source: TriggerSource = TriggerSource.APP,
) -> tuple[Incident, list[NotificationJob], bool]:
    """Create an SOS incident and its notification jobs in ONE transaction.

    Returns (incident, jobs, created). Idempotent on (user_ref, idempotency_key):
    a retried/duplicate SOS resolves to the same incident and does NOT enqueue a
    second wave of messages. The incident is committed together with its durable
    notification jobs, so we never acknowledge "queued" without the jobs existing.
    """
    # Local import avoids a module-load cycle (notifier imports models only).
    from .services.notifier import build_jobs_for_incident

    if data.idempotency_key:
        existing = get_incident_by_idempotency(session, user_ref, data.idempotency_key)
        if existing is not None:
            return existing, jobs_for_incident(session, existing.id), False

    incident = Incident(
        user_ref=user_ref,
        trigger_source=trigger_source,
        incident_type=data.incident_type,
        note=data.note,
        latitude=data.latitude,
        longitude=data.longitude,
        location_accuracy=data.location_accuracy,
        address=data.address,
        idempotency_key=data.idempotency_key,
        # An app SOS is a confirmed, active emergency (the user tapped & confirmed).
        status=IncidentStatus.ACTIVE,
    )
    session.add(incident)
    session.flush()  # assign incident.id before building jobs that reference it

    jobs = build_jobs_for_incident(session, incident, list(contacts), victim_name=victim_name)

    # Single commit: incident + all jobs are durable together, or neither is.
    session.commit()
    session.refresh(incident)
    for job in jobs:
        session.refresh(job)
    return incident, jobs, True


def cancel_incident(session: Session, user_ref: str, incident_id: str) -> Incident | None:
    incident = session.get(Incident, incident_id)
    if incident is None or incident.user_ref != user_ref:
        return None
    incident.status = IncidentStatus.CANCELLED
    incident.cancelled_at = utcnow()
    incident.updated_at = utcnow()
    session.add(incident)
    session.commit()
    session.refresh(incident)
    return incident
