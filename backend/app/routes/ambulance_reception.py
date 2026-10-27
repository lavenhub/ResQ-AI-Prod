"""Reception side of a WebRTC ambulance call (the FREE, no-PSTN path).

A teammate acting as the hospital/ambulance receptionist opens the reception page
in their browser and answers the call. Their browser sends a WebRTC SDP offer
here; we run the AI ambulance-coordinator pipeline over SmallWebRTC (same
transport as the in-app dispatcher) and return the SDP answer.

These endpoints are intentionally UNauthenticated: the receptionist is not the
booking owner and may not have an account. Access is gated by the booking id
(an unguessable uuid4) acting as a capability, plus the booking having to be in a
joinable (waiting / connected) state. This is a demo-scoped design and is
documented as such — a production deployment would issue a signed, expiring
reception token instead.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from .. import ambulance_service
from ..config import get_settings
from ..db import session_scope
from ..models import AmbulanceService, CallStatus, IN_FLIGHT_CALL_STATES, TERMINAL_BOOKING_STATES
from ..webrtc_ice import ice_servers_aiortc

try:
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    _WEBRTC_IMPORT_ERROR: str | None = None
except Exception as e:  # pragma: no cover
    SmallWebRTCConnection = None  # type: ignore[assignment]
    _WEBRTC_IMPORT_ERROR = str(e)

router = APIRouter(prefix="/api/ambulance/reception")

# Call statuses from which a receptionist may still join/answer.
_JOINABLE = set(IN_FLIGHT_CALL_STATES) | {CallStatus.PENDING}


class ReceptionOffer(BaseModel):
    sdp: str
    type: str
    booking_id: str


@router.get("/booking/{booking_id}")
async def reception_booking_info(booking_id: str) -> dict:
    """Minimal context the receptionist sees BEFORE answering (what's incoming).

    Deliberately limited: the emergency + pickup details needed to coordinate an
    ambulance, nothing more (no owner identity, phone, or internal ids)."""
    with session_scope() as s:
        booking = ambulance_service.get_booking(s, booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        svc = s.get(AmbulanceService, booking.service_id)
        return {
            "booking_id": booking.id,
            "service_name": svc.name if svc else None,
            "is_demo": bool(svc and svc.is_demo),
            "emergency_type": booking.emergency_type,
            "patient_condition": booking.patient_condition,
            "num_patients": booking.num_patients,
            "pickup_address": booking.pickup_address,
            "call_status": booking.call_status.value,
            "booking_state": booking.booking_state.value,
            "answerable": (
                booking.call_status in _JOINABLE
                and booking.booking_state not in TERMINAL_BOOKING_STATES
            ),
        }


@router.post("/offer")
async def reception_offer(body: ReceptionOffer, request: Request) -> dict:
    """WebRTC handshake for the receptionist. Runs the AI coordinator pipeline."""
    settings = get_settings()
    if not settings.voice_enabled:
        raise HTTPException(
            status_code=503,
            detail="Voice agent not configured (no GEMINI_API_KEY); cannot answer the call.",
        )
    if SmallWebRTCConnection is None:
        raise HTTPException(status_code=503, detail=f"WebRTC unavailable: {_WEBRTC_IMPORT_ERROR}")

    # Validate the booking is real and still joinable.
    with session_scope() as s:
        booking = ambulance_service.get_booking(s, body.booking_id)
        if booking is None:
            raise HTTPException(status_code=404, detail="Booking not found")
        if booking.booking_state in TERMINAL_BOOKING_STATES or booking.call_status not in _JOINABLE:
            raise HTTPException(status_code=409, detail="This call is no longer answerable.")
        svc = s.get(AmbulanceService, booking.service_id)
        is_demo = bool(svc and svc.is_demo)

    manager = request.app.state.session_manager
    tasks: set = request.app.state.voice_tasks

    connection = SmallWebRTCConnection(ice_servers=await ice_servers_aiortc())
    await connection.initialize(sdp=body.sdp, type=body.type)
    session_id = getattr(connection, "pc_id", None) or getattr(connection, "id", None) or body.booking_id

    if not await manager.try_acquire(f"recv-{session_id}"):
        await connection.disconnect()
        raise HTTPException(status_code=429, detail="Server at voice-session capacity; try shortly.")

    booking_id = body.booking_id

    async def _on_connected() -> None:
        def _work() -> None:
            with session_scope() as s:
                b = ambulance_service.get_booking(s, booking_id)
                if b is not None and b.call_status != CallStatus.CONNECTED:
                    ambulance_service.apply_call_status(s, b, CallStatus.CONNECTED)
        await asyncio.to_thread(_work)
        logger.info(f"[reception] receptionist connected for booking {booking_id}")

    async def _on_finished() -> None:
        await manager.release(f"recv-{session_id}")

        def _work() -> None:
            with session_scope() as s:
                b = ambulance_service.get_booking(s, booking_id)
                if b is not None and b.call_status not in {
                    CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED,
                    CallStatus.BUSY, CallStatus.NO_ANSWER,
                }:
                    ambulance_service.apply_call_status(
                        s, b, CallStatus.COMPLETED, detail="reception call ended"
                    )
        await asyncio.to_thread(_work)

    # Import here so non-voice deployments don't import the heavy pipeline.
    from ..voice.ambulance_pipeline import run_ambulance_call_webrtc

    task = asyncio.create_task(
        run_ambulance_call_webrtc(
            connection,
            booking_id=booking_id,
            is_demo=is_demo,
            on_connected=_on_connected,
            on_finished=_on_finished,
        )
    )
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    answer = connection.get_answer()
    logger.info(f"[reception] answering WebRTC offer for booking {booking_id}")
    return answer
