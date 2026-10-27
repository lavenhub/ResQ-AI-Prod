"""Ambulance coordination API — LOCAL simulated-call workflow (authenticated).

Directory + bookings + a text "reception call" where the AI coordinator talks to
a human receptionist (the presenter). No telephony, no WebRTC — just HTTPS Gemini
calls. The backend owns the booking state + records the outcome.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlmodel import Session

from .. import ambulance_service
from ..agent import AgentError
from ..ambulance_agent import coordinator_reply, extract_outcome
from ..ambulance_service import BookingError
from ..auth import get_current_user
from ..db import get_session
from ..models import (
    AmbulanceBooking,
    AmbulanceService,
    AmbulanceServiceRead,
    BookingCreate,
    BookingRead,
)
from ..services.phone import mask_phone

router = APIRouter(prefix="/api/ambulance")


class TranscriptItem(BaseModel):
    role: str  # "ai" (coordinator) or "receptionist"
    text: str


class CallTurn(BaseModel):
    transcript: list[TranscriptItem] = []


def _service_read(svc: AmbulanceService) -> AmbulanceServiceRead:
    r = AmbulanceServiceRead.model_validate(svc)
    r.phone = mask_phone(svc.phone)
    return r


def _booking_read(session: Session, booking: AmbulanceBooking) -> BookingRead:
    r = BookingRead.model_validate(booking)
    svc = session.get(AmbulanceService, booking.service_id)
    if svc is not None:
        r.service_name = svc.name
        r.service_phone_masked = mask_phone(svc.phone)
        r.service_is_demo = svc.is_demo
    return r


@router.get("/services", response_model=list[AmbulanceServiceRead])
async def list_services(
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[AmbulanceServiceRead]:
    ambulance_service.seed_services(session)  # idempotent: ensures demo hospitals exist
    return [_service_read(s) for s in ambulance_service.list_services(session)]


@router.post("/bookings", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    try:
        booking = ambulance_service.create_booking(session, user_ref, body)
    except BookingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _booking_read(session, booking)


@router.get("/bookings/{booking_id}", response_model=BookingRead)
async def get_booking(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return _booking_read(session, booking)


class CallOpenResponse(BaseModel):
    booking: BookingRead
    reply: str


@router.post("/bookings/{booking_id}/call/open", response_model=CallOpenResponse)
async def call_open(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CallOpenResponse:
    """Start the reception call: the AI coordinator's opening line (it discloses it
    is an AI and explains the emergency + location)."""
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    booking = ambulance_service.open_call(session, booking)
    ctx = ambulance_service.booking_context(session, booking)
    try:
        reply = await coordinator_reply(ctx, [])
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=f"Assistant unavailable: {exc}") from exc
    return CallOpenResponse(booking=_booking_read(session, booking), reply=reply)


class CallTurnResponse(BaseModel):
    reply: str


@router.post("/bookings/{booking_id}/call/turn", response_model=CallTurnResponse)
async def call_turn(
    booking_id: str,
    body: CallTurn,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CallTurnResponse:
    """The AI coordinator's next line, given the call so far (the human
    receptionist's latest message is the last transcript item)."""
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    ambulance_service.mark_discussing(session, booking)
    ctx = ambulance_service.booking_context(session, booking)
    try:
        reply = await coordinator_reply(ctx, [t.model_dump() for t in body.transcript])
    except AgentError as exc:
        raise HTTPException(status_code=502, detail=f"Assistant unavailable: {exc}") from exc
    return CallTurnResponse(reply=reply)


@router.post("/bookings/{booking_id}/call/finalize", response_model=BookingRead)
async def call_finalize(
    booking_id: str,
    body: CallTurn,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    """End the call: extract the structured outcome from the transcript and record
    it (availability / ETA / charges / reference / confirmed)."""
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    outcome = await extract_outcome([t.model_dump() for t in body.transcript])
    booking = ambulance_service.apply_outcome(session, booking, outcome)
    return _booking_read(session, booking)
