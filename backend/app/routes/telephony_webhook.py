"""Telephony provider webhooks + the real-time media WebSocket.

Three provider-facing endpoints (mission Phase 5):
  * POST /twiml/{booking_id}   — fetched by the provider when the call is
    answered; returns the call-answer document wiring audio to our media stream.
  * POST /status/{booking_id}  — provider call-status callbacks; verified, then
    fed to the backend state machine.
  * WS   /media/{booking_id}   — the bidirectional media stream; runs the Gemini
    Live ambulance pipeline for the duration of the call.

Every HTTP webhook is signature-verified before it is allowed to change any
state. The media socket is only used by a real provider (never the mock).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

from fastapi import APIRouter, Request, Response, WebSocket
from loguru import logger

from .. import ambulance_service
from ..config import get_settings
from ..db import session_scope
from ..models import AmbulanceService, BookingState, CallStatus
from ..services.telephony import get_telephony_provider

router = APIRouter(prefix="/api/ambulance/telephony")


def _expected_url(request: Request) -> str:
    """The public URL the provider signed against (base + path), independent of
    any proxy rewriting of the Host header."""
    base = get_settings().telephony_base_url.rstrip("/")
    return f"{base}{request.url.path}"


async def _read_webhook(request: Request) -> tuple[bytes, dict[str, str]]:
    """Return (raw_body, params). Telephony providers (Twilio/Exotel) POST
    ``application/x-www-form-urlencoded``; we parse it with stdlib so no
    ``python-multipart`` dependency is needed. JSON bodies are also tolerated."""
    raw = await request.body()
    ctype = request.headers.get("content-type", "")
    if "application/json" in ctype:
        try:
            data = json.loads(raw or b"{}")
            return raw, {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return raw, {}
    return raw, dict(parse_qsl(raw.decode("utf-8", "ignore")))


@router.post("/twiml/{booking_id}")
async def twiml(booking_id: str, request: Request) -> Response:
    """Return the call-answer document that connects the call's audio to our
    media WebSocket. Fetched by the provider the instant the receptionist answers."""
    settings = get_settings()
    provider = get_telephony_provider()
    raw, form = await _read_webhook(request)
    if not provider.verify_webhook(
        url=_expected_url(request), raw_body=raw, form=form, headers=dict(request.headers)
    ):
        logger.warning(f"[telephony] rejected unsigned TwiML request for {booking_id}")
        return Response(status_code=403)

    ws_base = settings.media_ws_base().rstrip("/")
    media_ws_url = f"{ws_base}/api/ambulance/telephony/media/{booking_id}"
    doc = provider.build_call_answer_document(media_ws_url=media_ws_url, booking_id=booking_id)
    return Response(content=doc.body, media_type=doc.media_type)


@router.post("/status/{booking_id}")
async def status_callback(booking_id: str, request: Request) -> dict:
    """Apply a provider call-status callback to the booking's state machine."""
    provider = get_telephony_provider()
    raw, form = await _read_webhook(request)
    if not provider.verify_webhook(
        url=_expected_url(request), raw_body=raw, form=form, headers=dict(request.headers)
    ):
        logger.warning(f"[telephony] rejected unsigned status callback for {booking_id}")
        return Response(status_code=403)  # type: ignore[return-value]

    update = provider.parse_status_webhook(form)
    if update is None:
        return {"ok": True, "applied": False}

    with session_scope() as s:
        booking = ambulance_service.get_booking(s, booking_id)
        if booking is None:
            # Fall back to correlating by provider call id.
            booking = ambulance_service.get_booking_by_call_id(s, update.provider_call_id)
        if booking is None:
            logger.warning(f"[telephony] status for unknown booking {booking_id}")
            return {"ok": True, "applied": False}

        was_connected = booking.call_connected_at is not None
        prior_state = booking.booking_state
        booking = ambulance_service.apply_call_status(
            s, booking, update.status, detail=update.raw_status
        )

        # Authorized email updates on decisive telephony transitions (honest
        # status only; booking confirm/decline emails are queued by the tool).
        if update.status == CallStatus.CONNECTED and not was_connected:
            ambulance_service.enqueue_booking_update(s, booking, "connected")
        elif (
            booking.booking_state == BookingState.CALL_FAILED
            and prior_state != BookingState.CALL_FAILED
        ):
            ambulance_service.enqueue_booking_update(s, booking, "failed")

    return {"ok": True, "applied": True, "status": update.status.value}


@router.websocket("/media/{booking_id}")
async def media(websocket: WebSocket, booking_id: str) -> None:
    """The bidirectional media stream. Reads the provider's stream-'start'
    message, builds the serializer, and runs the Gemini Live ambulance pipeline."""
    await websocket.accept()
    settings = get_settings()
    provider = get_telephony_provider()

    if not settings.voice_enabled:
        logger.warning("[telephony] media socket opened but voice is disabled (no GEMINI_API_KEY)")
        await websocket.close(code=1011)
        return
    if provider.is_mock:
        logger.warning("[telephony] media socket opened in MOCK mode — closing (no real media)")
        await websocket.close(code=1011)
        return

    # Consume provider framing until the 'start' event (which carries the stream
    # + call ids the serializer needs), then hand the socket to the transport.
    start_payload: dict | None = None
    try:
        for _ in range(5):
            raw = await websocket.receive_text()
            data = json.loads(raw)
            event = data.get("event")
            if event == "start":
                start_payload = data
                break
            if event in ("stop", "closed"):
                await websocket.close()
                return
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[telephony] failed reading media start for {booking_id}: {exc}")
        await websocket.close(code=1011)
        return

    if start_payload is None:
        await websocket.close(code=1011)
        return

    serializer = provider.build_media_serializer(start_payload)

    # Mark CONNECTED + resolve demo flag.
    is_demo = False
    with session_scope() as s:
        booking = ambulance_service.get_booking(s, booking_id)
        if booking is not None:
            svc = s.get(AmbulanceService, booking.service_id)
            is_demo = bool(svc and svc.is_demo)
            if booking.call_status != CallStatus.CONNECTED:
                ambulance_service.apply_call_status(s, booking, CallStatus.CONNECTED)

    # Import here so mock-mode deployments (and tests) never import the heavy
    # Gemini-Live pipeline.
    from ..voice.ambulance_pipeline import run_ambulance_call

    async def _on_finished() -> None:
        # On media end, if the call didn't reach a decisive outcome, record the
        # disconnect so the UI/state is honest.
        with session_scope() as s:
            b = ambulance_service.get_booking(s, booking_id)
            if b is not None and b.call_status not in {
                CallStatus.COMPLETED, CallStatus.CANCELLED, CallStatus.FAILED,
                CallStatus.BUSY, CallStatus.NO_ANSWER,
            }:
                ambulance_service.apply_call_status(
                    s, b, CallStatus.COMPLETED, detail="media stream ended"
                )

    await run_ambulance_call(
        websocket, serializer, booking_id=booking_id, is_demo=is_demo, on_finished=_on_finished
    )
