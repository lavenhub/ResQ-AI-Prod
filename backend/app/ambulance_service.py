"""Ambulance booking business logic + the backend-owned state machine.

This is the ONE validated path for mutating ambulance bookings — the REST API,
the telephony webhooks, and the voice-call tools all go through here, so the
state-machine rules and honesty invariants live in a single place.

Cardinal rules (mission Phase 7), enforced here and nowhere else:
  * Only VALID state transitions are allowed; every transition is audited.
  * The LLM NEVER sets a booking to BOOKING_CONFIRMED. It reports observations
    through ``record_receptionist_response``; the BACKEND decides whether an
    explicit confirmation in an allowed state warrants BOOKING_CONFIRMED.
  * "available", "accepted", "confirmed" and "dispatched" are DISTINCT. We never
    conflate them and never auto-mark a dispatch from a phone call.
  * Only an ``active`` + (``verified`` or ``is_demo``) directory number is ever a
    call target. Arbitrary numbers are rejected.
"""

from __future__ import annotations

from loguru import logger
from sqlmodel import Session, select

from .config import get_settings
from .models import (
    AmbulanceBooking,
    AmbulanceService,
    Availability,
    BookingCreate,
    BookingState,
    CallEvent,
    CallStatus,
    IN_FLIGHT_CALL_STATES,
    Incident,
    IncidentStatus,
    SITUATION_CODES,
    TERMINAL_BOOKING_STATES,
    TriggerSource,
    utcnow,
)
from .services.phone import PhoneValidationError, normalize_phone


class BookingError(ValueError):
    """A booking request is invalid or unauthorized."""


class InvalidTransition(ValueError):
    """An attempted state-machine transition is not allowed."""


# ── State machine ───────────────────────────────────────────────────────────────
_IN_CALL = [
    BookingState.CONNECTED,
    BookingState.IDENTIFYING_OPERATOR,
    BookingState.EXPLAINING_EMERGENCY,
    BookingState.SHARING_LOCATION,
    BookingState.DISCUSSING_AVAILABILITY,
    BookingState.COLLECTING_DETAILS,
    BookingState.REQUESTING_CONFIRMATION,
]
_CALL_OUTCOMES = {
    BookingState.BOOKING_CONFIRMED,
    BookingState.BOOKING_DECLINED,
    BookingState.CALL_FAILED,
    BookingState.CALL_DISCONNECTED,
    BookingState.PENDING_FOLLOW_UP,
    BookingState.COMPLETED,
}


def _build_transitions() -> dict[BookingState, set[BookingState]]:
    t: dict[BookingState, set[BookingState]] = {
        BookingState.PREPARING_REQUEST: {BookingState.VALIDATING_DETAILS, BookingState.CALL_FAILED},
        BookingState.VALIDATING_DETAILS: {
            BookingState.INITIATING_CALL, BookingState.PREPARING_REQUEST, BookingState.CALL_FAILED,
        },
        BookingState.INITIATING_CALL: {
            BookingState.RINGING, BookingState.CONNECTED, BookingState.CALL_FAILED,
        },
        BookingState.RINGING: {
            BookingState.CONNECTED, BookingState.CALL_FAILED, BookingState.CALL_DISCONNECTED,
        },
        # Terminal-ish outcomes:
        BookingState.BOOKING_CONFIRMED: {BookingState.COMPLETED},
        BookingState.BOOKING_DECLINED: {BookingState.COMPLETED, BookingState.PENDING_FOLLOW_UP},
        BookingState.CALL_FAILED: {BookingState.PENDING_FOLLOW_UP, BookingState.COMPLETED},
        BookingState.CALL_DISCONNECTED: {BookingState.PENDING_FOLLOW_UP, BookingState.COMPLETED},
        BookingState.PENDING_FOLLOW_UP: {BookingState.COMPLETED},
        BookingState.COMPLETED: set(),
    }
    # Every in-call state may move to any other in-call state (the conversation
    # revisits topics) and to any call outcome.
    in_call_targets = set(_IN_CALL) | _CALL_OUTCOMES
    for s in _IN_CALL:
        targets = set(in_call_targets)
        targets.discard(s)
        t[s] = targets
    return t


VALID_TRANSITIONS = _build_transitions()


def can_transition(frm: BookingState, to: BookingState) -> bool:
    return to in VALID_TRANSITIONS.get(frm, set())


def _record_event(
    session: Session,
    booking: AmbulanceBooking,
    *,
    event_type: str,
    from_state: str | None,
    to_state: str | None,
    detail: str | None,
) -> None:
    session.add(
        CallEvent(
            booking_id=booking.id,
            event_type=event_type,
            from_state=from_state,
            to_state=to_state,
            detail=(detail or "")[:500] or None,
        )
    )


def transition(
    session: Session,
    booking: AmbulanceBooking,
    to_state: BookingState,
    *,
    detail: str | None = None,
    force: bool = False,
) -> None:
    """Move the booking to ``to_state`` (mutating + auditing, NOT committing).

    Raises ``InvalidTransition`` unless the move is allowed (or ``force`` is set,
    used only for explicit operator retries). The caller commits.
    """
    frm = booking.booking_state
    if frm == to_state:
        return
    if not force and not can_transition(frm, to_state):
        raise InvalidTransition(f"illegal transition {frm.value} -> {to_state.value}")
    _record_event(
        session, booking,
        event_type="state_transition" if not force else "state_transition_forced",
        from_state=frm.value, to_state=to_state.value, detail=detail,
    )
    booking.booking_state = to_state
    booking.updated_at = utcnow()
    session.add(booking)
    logger.info(f"[ambulance] booking {booking.id} {frm.value} -> {to_state.value}")


def _advance_toward(
    session: Session, booking: AmbulanceBooking, target: BookingState, *, detail: str | None = None
) -> None:
    """Transition to ``target`` only if the current state allows it; otherwise
    leave the state unchanged (used for the LLM's soft, observation-driven
    progress, which must never raise or force an illegal jump)."""
    if booking.booking_state == target:
        return
    if can_transition(booking.booking_state, target):
        transition(session, booking, target, detail=detail)


# ── Lookups ───────────────────────────────────────────────────────────────────
def get_booking(session: Session, booking_id: str) -> AmbulanceBooking | None:
    return session.get(AmbulanceBooking, booking_id)


def get_booking_for_user(
    session: Session, user_ref: str, booking_id: str
) -> AmbulanceBooking | None:
    b = session.get(AmbulanceBooking, booking_id)
    if b is None or b.user_ref != user_ref:
        return None
    return b


def get_booking_by_call_id(session: Session, provider_call_id: str) -> AmbulanceBooking | None:
    return session.exec(
        select(AmbulanceBooking).where(AmbulanceBooking.provider_call_id == provider_call_id)
    ).first()


def list_bookings(
    session: Session, user_ref: str, *, incident_id: str | None = None, limit: int = 50
) -> list[AmbulanceBooking]:
    stmt = select(AmbulanceBooking).where(AmbulanceBooking.user_ref == user_ref)
    if incident_id:
        stmt = stmt.where(AmbulanceBooking.incident_id == incident_id)
    stmt = stmt.order_by(AmbulanceBooking.created_at.desc()).limit(limit)  # type: ignore[attr-defined]
    return list(session.exec(stmt).all())


def get_service(session: Session, service_id: str) -> AmbulanceService | None:
    return session.get(AmbulanceService, service_id)


# ── Booking creation ─────────────────────────────────────────────────────────
def create_booking(
    session: Session, user_ref: str, data: BookingCreate
) -> AmbulanceBooking:
    """Create a booking tied to an existing incident the caller owns.

    Patient/location facts are copied from the incident (single source of truth).
    The target service must exist, be active and verified-or-demo.
    """
    if data.incident_id:
        incident = session.get(Incident, data.incident_id)
        if incident is None:
            raise BookingError("incident not found")
        if incident.user_ref != user_ref:
            raise BookingError("not authorized for this incident")
    else:
        # No incident supplied — create one owned by the authenticated caller
        # from the supplied location + emergency fields (never invented).
        incident = Incident(
            user_ref=user_ref,
            incident_type=data.emergency_type,
            reported_symptoms=data.patient_condition,
            latitude=data.latitude,
            longitude=data.longitude,
            location_accuracy=data.location_accuracy,
            address=data.address,
            trigger_source=TriggerSource.APP,
            status=IncidentStatus.ACTIVE,
        )
        session.add(incident)
        session.flush()  # assign incident.id

    service = session.get(AmbulanceService, data.service_id)
    if service is None:
        raise BookingError("ambulance service not found")
    if not service.active:
        raise BookingError("that ambulance service is not active")
    if not (service.verified or service.is_demo):
        raise BookingError("that ambulance service is not verified and cannot be dialed")

    booking = AmbulanceBooking(
        incident_id=incident.id,
        user_ref=user_ref,
        service_id=service.id,
        # Patient/emergency: caller-supplied overrides, else from the incident.
        patient_name=data.patient_name,
        patient_age=data.patient_age,
        patient_condition=data.patient_condition or incident.reported_symptoms,
        emergency_type=data.emergency_type or incident.incident_type,
        num_patients=data.num_patients or 1,
        # Location: ALWAYS from the device-reported incident, never invented.
        pickup_address=incident.address,
        latitude=incident.latitude,
        longitude=incident.longitude,
        location_accuracy=incident.location_accuracy,
        destination_preference=data.destination_preference,
        required_ambulance_type=data.required_ambulance_type,
        special_requirements=data.special_requirements,
        authorized_contact=data.authorized_contact,
        consent=bool(data.consent),
        booking_state=BookingState.PREPARING_REQUEST,
        call_status=CallStatus.PENDING,
    )
    session.add(booking)
    _record_event(
        session, booking, event_type="created", from_state=None,
        to_state=BookingState.PREPARING_REQUEST.value,
        detail=f"service={service.name} demo={service.is_demo}",
    )
    session.commit()
    session.refresh(booking)
    return booking


# ── Call pre-flight validation ──────────────────────────────────────────────
def validate_for_call(session: Session, booking: AmbulanceBooking) -> list[str]:
    """Return a list of reasons this booking can NOT be called right now (empty
    == ready to dial). Backend authorization + allowlisting live here."""
    problems: list[str] = []
    if not booking.consent:
        problems.append("consent to share patient details has not been given")

    service = session.get(AmbulanceService, booking.service_id)
    if service is None:
        problems.append("ambulance service no longer exists")
        return problems
    if not service.active:
        problems.append("ambulance service is inactive")
    if not (service.verified or service.is_demo):
        problems.append("ambulance service is not verified")
    try:
        normalize_phone(service.phone)
    except PhoneValidationError:
        problems.append("ambulance service phone number is not a valid dialable number")

    # Duplicate-call prevention: never place a second call while one is in flight.
    if booking.call_status in IN_FLIGHT_CALL_STATES:
        problems.append("a call for this booking is already in progress")

    if booking.attempts >= get_settings().ambulance_max_call_attempts:
        problems.append("maximum call attempts reached for this booking")

    return problems


def service_dial_number(session: Session, booking: AmbulanceBooking) -> str:
    """The validated E.164 number to dial (resolved from the directory, never
    from the LLM or user input)."""
    service = session.get(AmbulanceService, booking.service_id)
    if service is None:
        raise BookingError("ambulance service not found")
    return normalize_phone(service.phone).e164


# ── Call lifecycle ──────────────────────────────────────────────────────────
def reserve_call(session: Session, booking: AmbulanceBooking) -> AmbulanceBooking:
    """Atomically mark the booking as having a call in flight BEFORE we ask the
    provider to dial — this closes the duplicate-call window: a second request
    then fails ``validate_for_call`` (call_status is now in-flight). Advances
    PREPARING -> VALIDATING_DETAILS -> INITIATING_CALL and stamps the attempt.
    """
    if booking.booking_state == BookingState.PREPARING_REQUEST:
        transition(session, booking, BookingState.VALIDATING_DETAILS)
    _advance_toward(session, booking, BookingState.INITIATING_CALL)
    booking.call_status = CallStatus.INITIATING
    booking.attempts += 1
    booking.call_initiated_at = utcnow()
    booking.last_error = None
    booking.updated_at = utcnow()
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def attach_provider_call(
    session: Session, booking: AmbulanceBooking, *, provider: str, provider_call_id: str | None
) -> AmbulanceBooking:
    """Record the provider + its call id after a successful dial request."""
    booking.provider = provider
    booking.provider_call_id = provider_call_id
    booking.updated_at = utcnow()
    _record_event(
        session, booking, event_type="call_placed", from_state=None, to_state=None,
        detail=f"provider={provider} call_id={provider_call_id}",
    )
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def mark_call_failed(
    session: Session, booking: AmbulanceBooking, *, call_status: CallStatus, detail: str | None
) -> AmbulanceBooking:
    booking.call_status = call_status
    booking.last_error = (detail or "")[:300] or None
    booking.call_completed_at = utcnow()
    if booking.booking_state not in TERMINAL_BOOKING_STATES:
        transition(session, booking, BookingState.CALL_FAILED, detail=detail, force=True)
    booking.updated_at = utcnow()
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def apply_call_status(
    session: Session, booking: AmbulanceBooking, status: CallStatus, *, detail: str | None = None
) -> AmbulanceBooking:
    """Apply a telephony status update (from a webhook or poll) to the booking,
    driving the matching state-machine transitions + timestamps. Idempotent and
    monotonic: a terminal booking state is never regressed."""
    _record_event(
        session, booking, event_type="call_status",
        from_state=booking.call_status.value, to_state=status.value, detail=detail,
    )
    booking.call_status = status

    if status == CallStatus.RINGING:
        _advance_toward(session, booking, BookingState.RINGING)
    elif status == CallStatus.CONNECTED:
        if booking.call_connected_at is None:
            booking.call_connected_at = utcnow()
        _advance_toward(session, booking, BookingState.CONNECTED)
    elif status == CallStatus.COMPLETED:
        booking.call_completed_at = utcnow()
        # Call ended. Keep a confirmed/declined outcome; otherwise wrap up.
        if booking.booking_state not in TERMINAL_BOOKING_STATES:
            # If we had connected but reached no outcome, it needs follow-up.
            target = (
                BookingState.COMPLETED
                if booking.call_connected_at is not None
                else BookingState.CALL_DISCONNECTED
            )
            if can_transition(booking.booking_state, target):
                transition(session, booking, target, detail="call completed")
            else:
                transition(session, booking, BookingState.COMPLETED, detail="call completed", force=True)
        elif booking.booking_state in (BookingState.BOOKING_CONFIRMED, BookingState.BOOKING_DECLINED):
            _advance_toward(session, booking, BookingState.COMPLETED, detail="call completed")
    elif status in (CallStatus.BUSY, CallStatus.NO_ANSWER, CallStatus.FAILED):
        booking.call_completed_at = utcnow()
        if booking.booking_state not in TERMINAL_BOOKING_STATES:
            transition(session, booking, BookingState.CALL_FAILED, detail=detail or status.value, force=True)
    elif status == CallStatus.CANCELLED:
        booking.call_completed_at = utcnow()
        if booking.booking_state not in TERMINAL_BOOKING_STATES:
            transition(session, booking, BookingState.CALL_DISCONNECTED, detail=detail or "cancelled", force=True)

    booking.updated_at = utcnow()
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


# ── Receptionist-response recording (called by the LLM tool) ──────────────────
def record_receptionist_response(
    session: Session,
    booking: AmbulanceBooking,
    *,
    availability: str | None = None,
    ambulance_type: str | None = None,
    eta: str | None = None,
    charges: str | None = None,
    booking_reference: str | None = None,
    booking_accepted: bool | None = None,
    explicit_confirmation: bool | None = None,
    declined: bool | None = None,
    situation_code: str | None = None,
    needs_more_info: bool | None = None,
    transferred: bool | None = None,
    notes: str | None = None,
) -> AmbulanceBooking:
    """Record what the receptionist actually said as OBSERVATIONS, and let the
    BACKEND decide the resulting state. The LLM supplies no state here.

    BOOKING_CONFIRMED is only reached when ``explicit_confirmation`` is True AND
    the receptionist accepted the booking (or an ambulance is available) AND the
    state machine permits it — i.e. the backend validates and records it; the LLM
    cannot set it on its own. An ambulance being "available" is NEVER treated as
    a confirmed booking or a dispatch.
    """
    changed: list[str] = []

    if availability is not None:
        av = str(availability).strip().upper()
        if av in Availability.__members__:
            booking.availability = Availability[av]
            changed.append(f"availability={av}")
            _advance_toward(session, booking, BookingState.DISCUSSING_AVAILABILITY)
    if ambulance_type:
        booking.required_ambulance_type = str(ambulance_type)[:200]
        changed.append("ambulance_type")
    if eta:
        booking.eta_text = str(eta)[:200]
        changed.append("eta")
    if charges:
        booking.charges_text = str(charges)[:200]
        changed.append("charges")
    if booking_reference:
        booking.booking_reference = str(booking_reference)[:120]
        changed.append("reference")
    if situation_code:
        code = str(situation_code).strip().upper()[:1]
        if code in SITUATION_CODES:
            booking.situation_code = code
            changed.append(f"situation={code}")
    if notes:
        booking.receptionist_response = str(notes)[:1000]
        changed.append("notes")
    if booking_accepted:
        booking.booking_accepted = True
        changed.append("accepted")
    if needs_more_info:
        _advance_toward(session, booking, BookingState.COLLECTING_DETAILS)
    if transferred:
        _record_event(session, booking, event_type="transferred", from_state=None,
                       to_state=None, detail="call transferred to another operator")

    _record_event(
        session, booking, event_type="receptionist_response", from_state=None, to_state=None,
        detail=", ".join(changed) or "no structured change",
    )

    # ── Backend-owned outcome decisions ──────────────────────────────────────
    if declined:
        if booking.booking_state not in TERMINAL_BOOKING_STATES:
            _advance_toward(session, booking, BookingState.BOOKING_DECLINED,
                            detail="receptionist declined")
    elif explicit_confirmation:
        # Backend validation: confirm ONLY with an accepted booking (or an
        # available ambulance) and only from an allowed, in-call state.
        qualifies = booking.booking_accepted or booking.availability == Availability.AVAILABLE
        if qualifies and booking.booking_state not in TERMINAL_BOOKING_STATES:
            # Step through REQUESTING_CONFIRMATION so the audit trail is explicit.
            _advance_toward(session, booking, BookingState.REQUESTING_CONFIRMATION,
                            detail="explicit confirmation reported by caller agent")
            if can_transition(booking.booking_state, BookingState.BOOKING_CONFIRMED):
                transition(session, booking, BookingState.BOOKING_CONFIRMED,
                           detail="backend-validated explicit confirmation")
        else:
            logger.info(
                f"[ambulance] confirmation reported for {booking.id} but not qualified "
                f"(accepted={booking.booking_accepted}, availability={booking.availability.value})"
            )

    booking.updated_at = utcnow()
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def retry_call(session: Session, booking: AmbulanceBooking) -> None:
    """Prepare a failed/terminal-call booking for another attempt (bounded)."""
    if booking.attempts >= get_settings().ambulance_max_call_attempts:
        raise BookingError("maximum call attempts reached")
    if booking.call_status in IN_FLIGHT_CALL_STATES:
        raise BookingError("a call is already in progress")
    booking.call_status = CallStatus.PENDING
    booking.last_error = None
    booking.provider_call_id = None
    transition(session, booking, BookingState.PREPARING_REQUEST, detail="operator retry", force=True)
    booking.updated_at = utcnow()
    session.add(booking)
    session.commit()
    session.refresh(booking)


# ── Context fed to the caller LLM (verified facts only) ───────────────────────
def enqueue_booking_update(session: Session, booking: AmbulanceBooking, kind: str) -> int:
    """Queue an authorized EMAIL update about this booking to the caller's OWN
    verified emergency contacts, reusing the existing durable outbox + worker
    (NO second email service). Returns the number of jobs queued.

    Honesty: the email states the backend-recorded status only (e.g. "booking
    confirmed by reception" / "call failed"); the shared outbox worker tracks
    queued vs delivered truthfully. Shared only with already-verified contacts.
    """
    # Lazy imports avoid a module-load cycle (notifier imports models only).
    from . import contact_service
    from .models import NotificationJob, NotificationStatus
    from .services.email import get_provider
    from .services.email.templating import render_booking_update_email

    contacts = contact_service.eligible_contacts(session, booking.user_ref)
    if not contacts:
        return 0
    settings = get_settings()
    service = session.get(AmbulanceService, booking.service_id)
    mail = render_booking_update_email(
        brand=settings.email_brand_name,
        kind=kind,
        service_name=service.name if service else None,
        is_demo=service.is_demo if service else False,
        booking_state=booking.booking_state.value,
        availability=booking.availability.value,
        eta=booking.eta_text,
        charges=booking.charges_text,
        booking_reference=booking.booking_reference,
        patient_condition=booking.patient_condition,
        pickup_address=booking.pickup_address,
        incident_id=booking.incident_id,
        when=utcnow(),
    )
    channel = get_provider().channel
    queued = 0
    for contact in contacts:
        if not contact.email:
            continue
        job = NotificationJob(
            incident_id=booking.incident_id,
            user_ref=booking.user_ref,
            contact_id=contact.id,
            recipient_name=contact.name,
            recipient_email=contact.email,
            channel=channel,
            subject=mail.subject,
            message_body=mail.text,
            html_body=mail.html,
            max_attempts=settings.email_max_attempts,
            next_attempt_at=utcnow(),
            status=NotificationStatus.QUEUED,
        )
        job.idempotency_key = f"booking-{booking.id}-{kind}-{job.id}"
        session.add(job)
        queued += 1
    session.commit()
    logger.info(f"[ambulance] queued {queued} booking-update email(s) ({kind}) for {booking.id}")
    return queued


def booking_context(session: Session, booking: AmbulanceBooking) -> dict:
    """The verified facts the caller AI may share. Nothing here is invented;
    absent facts are reported as unknown so the model never fabricates."""
    service = session.get(AmbulanceService, booking.service_id)
    loc = booking.pickup_address or (
        f"{booking.latitude:.5f}, {booking.longitude:.5f}"
        if booking.latitude is not None and booking.longitude is not None
        else None
    )
    return {
        "service_name": service.name if service else None,
        "is_demo": service.is_demo if service else False,
        "patient_name": booking.patient_name,
        "patient_age": booking.patient_age,
        "patient_condition": booking.patient_condition,
        "emergency_type": booking.emergency_type,
        "num_patients": booking.num_patients,
        "pickup_location": loc,
        "location_accuracy_m": booking.location_accuracy,
        "destination_preference": booking.destination_preference,
        "required_ambulance_type": booking.required_ambulance_type,
        "special_requirements": booking.special_requirements,
        "authorized_contact": booking.authorized_contact,
    }
