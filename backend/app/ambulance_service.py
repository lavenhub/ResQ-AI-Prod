"""Ambulance booking + the LOCAL (simulated-call) coordination workflow.

No telephony, no WebRTC. A booking is created from an incident + a chosen hospital
from the directory; the "call" is a text/voice conversation between the AI
coordinator and a human receptionist (see ambulance_agent). The structured
outcome is applied here, advancing a small backend-owned state machine. Honesty
is preserved: an ambulance is "confirmed" only when the receptionist confirmed it,
and we never mark a real dispatch.
"""

from __future__ import annotations

from loguru import logger
from sqlmodel import Session, select

from .models import (
    AmbulanceBooking,
    AmbulanceService,
    Availability,
    BookingCreate,
    BookingState,
    CallEvent,
    CallStatus,
    Incident,
    IncidentStatus,
    TriggerSource,
    utcnow,
)


class BookingError(ValueError):
    """A booking request is invalid or unauthorized."""


# Seed hospitals so the demo works with no manual setup (idempotent).
_SEED_HOSPITALS = [
    {"name": "City General Hospital", "phone": "+912226001000", "city": "Mumbai",
     "ambulance_types": "Basic, ICU", "capabilities": "ER, trauma, cardiac"},
    {"name": "Apollo Emergency", "phone": "+912226002000", "city": "Mumbai",
     "ambulance_types": "Basic, ICU, cardiac", "capabilities": "ICU, ventilator"},
    {"name": "LifeLine Ambulance Service", "phone": "+912226003000", "city": "Mumbai",
     "ambulance_types": "Basic life support", "capabilities": "24x7 dispatch"},
]


def seed_services(session: Session) -> int:
    """Insert the demo hospital directory if the table is empty. Returns count added."""
    existing = session.exec(select(AmbulanceService).limit(1)).first()
    if existing is not None:
        return 0
    added = 0
    for h in _SEED_HOSPITALS:
        session.add(AmbulanceService(verified=True, active=True, is_demo=True, created_by="seed", **h))
        added += 1
    session.commit()
    logger.info(f"[ambulance] seeded {added} demo hospital(s)")
    return added


def list_services(session: Session) -> list[AmbulanceService]:
    return list(
        session.exec(
            select(AmbulanceService).where(AmbulanceService.active == True)  # noqa: E712
            .order_by(AmbulanceService.name)  # type: ignore[attr-defined]
        ).all()
    )


def get_service(session: Session, service_id: str) -> AmbulanceService | None:
    return session.get(AmbulanceService, service_id)


def get_booking(session: Session, booking_id: str) -> AmbulanceBooking | None:
    return session.get(AmbulanceBooking, booking_id)


def get_booking_for_user(session: Session, user_ref: str, booking_id: str) -> AmbulanceBooking | None:
    b = session.get(AmbulanceBooking, booking_id)
    if b is None or b.user_ref != user_ref:
        return None
    return b


def _event(session: Session, booking: AmbulanceBooking, event_type: str, detail: str | None,
           frm: str | None = None, to: str | None = None) -> None:
    session.add(CallEvent(booking_id=booking.id, event_type=event_type,
                          from_state=frm, to_state=to, detail=(detail or "")[:500] or None))


def create_booking(session: Session, user_ref: str, data: BookingCreate) -> AmbulanceBooking:
    """Create a booking tied to an incident the caller owns (or auto-create one)."""
    if data.incident_id:
        incident = session.get(Incident, data.incident_id)
        if incident is None:
            raise BookingError("incident not found")
        if incident.user_ref != user_ref:
            raise BookingError("not authorized for this incident")
    else:
        incident = Incident(
            user_ref=user_ref, incident_type=data.emergency_type,
            reported_symptoms=data.patient_condition,
            latitude=data.latitude, longitude=data.longitude,
            location_accuracy=data.location_accuracy, address=data.address,
            trigger_source=TriggerSource.APP, status=IncidentStatus.ACTIVE,
        )
        session.add(incident)
        session.flush()

    service = session.get(AmbulanceService, data.service_id)
    if service is None:
        raise BookingError("ambulance service not found")
    if not service.active:
        raise BookingError("that ambulance service is not active")

    booking = AmbulanceBooking(
        incident_id=incident.id, user_ref=user_ref, service_id=service.id,
        patient_name=data.patient_name, patient_age=data.patient_age,
        patient_condition=data.patient_condition or incident.reported_symptoms,
        emergency_type=data.emergency_type or incident.incident_type,
        num_patients=data.num_patients or 1,
        pickup_address=incident.address, latitude=incident.latitude,
        longitude=incident.longitude, location_accuracy=incident.location_accuracy,
        destination_preference=data.destination_preference,
        required_ambulance_type=data.required_ambulance_type,
        special_requirements=data.special_requirements,
        authorized_contact=data.authorized_contact, consent=bool(data.consent),
        booking_state=BookingState.PREPARING_REQUEST, call_status=CallStatus.PENDING,
        provider="simulated",
    )
    session.add(booking)
    _event(session, booking, "created", f"service={service.name}", to=BookingState.PREPARING_REQUEST.value)
    session.commit()
    session.refresh(booking)
    return booking


def open_call(session: Session, booking: AmbulanceBooking) -> AmbulanceBooking:
    """Mark the simulated reception call connected (the receptionist 'answered')."""
    booking.call_status = CallStatus.CONNECTED
    booking.booking_state = BookingState.CONNECTED
    if booking.call_connected_at is None:
        booking.call_connected_at = utcnow()
    booking.updated_at = utcnow()
    _event(session, booking, "call_opened", "receptionist answered (simulated)",
           to=BookingState.CONNECTED.value)
    session.add(booking)
    session.commit()
    session.refresh(booking)
    return booking


def mark_discussing(session: Session, booking: AmbulanceBooking) -> None:
    if booking.booking_state in (BookingState.CONNECTED, BookingState.PREPARING_REQUEST):
        booking.booking_state = BookingState.DISCUSSING_AVAILABILITY
        booking.updated_at = utcnow()
        session.add(booking)
        session.commit()


def apply_outcome(session: Session, booking: AmbulanceBooking, outcome: dict) -> AmbulanceBooking:
    """Apply the extracted call outcome, advancing the booking state (backend-owned)."""
    av = outcome.get("availability", "UNKNOWN")
    booking.availability = Availability[av] if av in Availability.__members__ else Availability.UNKNOWN
    booking.required_ambulance_type = outcome.get("ambulance_type") or booking.required_ambulance_type
    booking.eta_text = outcome.get("eta") or booking.eta_text
    booking.charges_text = outcome.get("charges") or booking.charges_text
    booking.booking_reference = outcome.get("booking_reference") or booking.booking_reference
    booking.receptionist_response = outcome.get("summary") or booking.receptionist_response
    booking.booking_accepted = bool(outcome.get("booking_confirmed"))

    frm = booking.booking_state.value
    if outcome.get("booking_confirmed"):
        booking.booking_state = BookingState.BOOKING_CONFIRMED
    elif outcome.get("declined"):
        booking.booking_state = BookingState.BOOKING_DECLINED
    else:
        booking.booking_state = BookingState.PENDING_FOLLOW_UP

    booking.call_status = CallStatus.COMPLETED
    booking.call_completed_at = utcnow()
    booking.updated_at = utcnow()
    _event(session, booking, "outcome", outcome.get("summary"), frm=frm, to=booking.booking_state.value)
    session.add(booking)
    session.commit()
    session.refresh(booking)
    logger.info(f"[ambulance] booking {booking.id} -> {booking.booking_state.value}")
    return booking


def booking_context(session: Session, booking: AmbulanceBooking) -> dict:
    """Verified facts the AI coordinator may share (nothing invented)."""
    service = session.get(AmbulanceService, booking.service_id)
    coords = (
        f"{booking.latitude:.5f}, {booking.longitude:.5f}"
        if booking.latitude is not None and booking.longitude is not None
        else None
    )
    return {
        "service_name": service.name if service else None,
        "patient_name": booking.patient_name,
        "patient_age": booking.patient_age,
        "patient_condition": booking.patient_condition,
        "emergency_type": booking.emergency_type,
        "num_patients": booking.num_patients,
        "pickup_address": booking.pickup_address,
        "coordinates": coords,
        "location_accuracy_m": booking.location_accuracy,
        "special_requirements": booking.special_requirements,
    }
