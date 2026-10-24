"""WebRTC signaling for the voice agent.

The existing frontend (`useVoiceAgent.ts`) POSTs an SDP offer here and sets the
returned SDP answer as its remote description — the exact Pipecat SmallWebRTC
handshake. We create a peer connection, launch the Gemini voice pipeline in the
background, and return the answer.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from ..auth import get_optional_user
from ..config import get_settings
from ..voice.pipeline import run_voice_bot

try:
    from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

    _WEBRTC_IMPORT_ERROR: str | None = None
except Exception as e:  # pragma: no cover - only if deps missing
    SmallWebRTCConnection = None  # type: ignore[assignment]
    _WEBRTC_IMPORT_ERROR = str(e)

router = APIRouter(prefix="/api/rtc")

_STUN = ["stun:stun.l.google.com:19302"]


class OfferRequest(BaseModel):
    sdp: str
    type: str
    language: str | None = "English"
    lat: float | None = None
    lng: float | None = None
    address: str | None = None
    accuracy: float | None = None


@router.post("/offer")
async def offer(
    body: OfferRequest,
    request: Request,
    user_ref: str | None = Depends(get_optional_user),
) -> dict:
    settings = get_settings()

    # Honest failure: no provider key => no real voice. Do not fake a session.
    if not settings.voice_enabled:
        raise HTTPException(
            status_code=503,
            detail="Voice agent not configured (no GEMINI_API_KEY). "
            "Set it on the server to enable live calls; meanwhile use the manual options.",
        )
    if SmallWebRTCConnection is None:
        raise HTTPException(
            status_code=503,
            detail=f"WebRTC backend unavailable: {_WEBRTC_IMPORT_ERROR}",
        )

    manager = request.app.state.session_manager
    tasks: set[asyncio.Task] = request.app.state.voice_tasks

    connection = SmallWebRTCConnection(ice_servers=_STUN)
    await connection.initialize(sdp=body.sdp, type=body.type)
    session_id = getattr(connection, "pc_id", None) or getattr(connection, "id", None) or "sess"

    if not await manager.try_acquire(session_id):
        await connection.disconnect()
        raise HTTPException(
            status_code=429,
            detail="Server is at voice-session capacity. Please try again shortly.",
        )

    async def _release() -> None:
        await manager.release(session_id)

    task = asyncio.create_task(
        run_voice_bot(
            connection,
            session_id=session_id,
            language_name=body.language or "English",
            user_ref=user_ref,
            latitude=body.lat,
            longitude=body.lng,
            location_accuracy=body.accuracy,
            address=body.address,
            on_finished=_release,
        )
    )
    # Keep a reference so the task isn't garbage-collected mid-call.
    tasks.add(task)
    task.add_done_callback(tasks.discard)

    answer = connection.get_answer()
    logger.info(f"[rtc] answering offer for session {session_id}")
    return answer
