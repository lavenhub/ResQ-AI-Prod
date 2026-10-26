"""State-machine + backend-confirmation-rule tests (service layer, direct DB).

Asserts the cardinal rule: the LLM-facing ``record_receptionist_response`` can
NEVER by itself drive a booking to BOOKING_CONFIRMED — the backend only confirms
an explicit confirmation that qualifies (accepted / available) from an allowed
state. "available" is never conflated with "confirmed".
"""

from __future__ import annotations

import pytest

from app import ambulance_service as svc
from app.ambulance_service import InvalidTransition, can_transition
from app.db import session_scope
from app.models import (
    AmbulanceBooking,
    AmbulanceService,
    Availability,
    BookingCreate,
    BookingState,
    CallStatus,
    Incident,
)

_USER = "+919300012345"


def _new_booking(client) -> str:
    """Create incident+service+booking directly and return the booking id."""
    with session_scope() as s:
        inc = Incident(user_ref=_USER, incident_type="Accident",
                       latitude=19.0, longitude=72.0, address="Somewhere")
        s.add(inc); s.commit(); s.refresh(inc)
        service = AmbulanceService(name="Hosp", phone="+919812345678",
                                   is_demo=True, verified=True, active=True)
        s.add(service); s.commit(); s.refresh(service)
        booking = svc.create_booking(
            s, _USER, BookingCreate(incident_id=inc.id, service_id=service.id, consent=True)
        )
        return booking.id


def _drive_to_connected(bid: str) -> None:
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        svc.reserve_call(s, b)
        svc.apply_call_status(s, b, CallStatus.CONNECTED)


def test_transition_table_rules():
    assert can_transition(BookingState.PREPARING_REQUEST, BookingState.VALIDATING_DETAILS)
    assert can_transition(BookingState.VALIDATING_DETAILS, BookingState.INITIATING_CALL)
    assert can_transition(BookingState.CONNECTED, BookingState.DISCUSSING_AVAILABILITY)
    assert can_transition(BookingState.REQUESTING_CONFIRMATION, BookingState.BOOKING_CONFIRMED)
    # Illegal jumps:
    assert not can_transition(BookingState.PREPARING_REQUEST, BookingState.BOOKING_CONFIRMED)
    assert not can_transition(BookingState.BOOKING_CONFIRMED, BookingState.DISCUSSING_AVAILABILITY)


def test_transition_raises_on_illegal(client):
    bid = _new_booking(client)
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        with pytest.raises(InvalidTransition):
            svc.transition(s, b, BookingState.BOOKING_CONFIRMED)


def test_availability_alone_does_not_confirm(client):
    bid = _new_booking(client)
    _drive_to_connected(bid)
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        b = svc.record_receptionist_response(s, b, availability="AVAILABLE")
        assert b.availability == Availability.AVAILABLE
        assert b.booking_state == BookingState.DISCUSSING_AVAILABILITY
        assert b.booking_state != BookingState.BOOKING_CONFIRMED
        assert b.booking_accepted is False


def test_confirmation_without_acceptance_does_not_confirm(client):
    bid = _new_booking(client)
    _drive_to_connected(bid)
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        # explicit_confirmation but nothing qualifies it (no accept, availability UNKNOWN).
        b = svc.record_receptionist_response(s, b, explicit_confirmation=True)
        assert b.booking_state != BookingState.BOOKING_CONFIRMED


def test_backend_confirms_only_qualified_explicit_confirmation(client):
    bid = _new_booking(client)
    _drive_to_connected(bid)
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        b = svc.record_receptionist_response(
            s, b, availability="AVAILABLE", booking_accepted=True,
            explicit_confirmation=True, eta="12 minutes", booking_reference="BK-900",
        )
        assert b.booking_state == BookingState.BOOKING_CONFIRMED
        assert b.booking_accepted is True
        assert b.eta_text == "12 minutes"
        assert b.booking_reference == "BK-900"


def test_decline_marks_declined(client):
    bid = _new_booking(client)
    _drive_to_connected(bid)
    with session_scope() as s:
        b = svc.get_booking(s, bid)
        b = svc.record_receptionist_response(s, b, availability="UNAVAILABLE", declined=True)
        assert b.booking_state == BookingState.BOOKING_DECLINED
