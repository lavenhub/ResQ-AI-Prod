"""Conversational agent endpoint (HTTP — works on any host).

The hosted web app / APK POST the running conversation here and get a text reply
from Gemini. No WebRTC, no server media — just request/response, so it runs fine
on Render. Speech (TTS/STT) is handled in the browser.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from ..agent import AgentError, agent_reply
from ..config import get_settings

router = APIRouter(prefix="/api/agent")

_MAX_MESSAGES = 40
_MAX_CHARS = 4000


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    language: str | None = "English"
    lat: float | None = None
    lng: float | None = None
    address: str | None = None

    @field_validator("messages")
    @classmethod
    def _trim(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("messages is required")
        # Keep only the most recent turns, and clamp message length (abuse guard).
        trimmed = v[-_MAX_MESSAGES:]
        for m in trimmed:
            if m.content and len(m.content) > _MAX_CHARS:
                m.content = m.content[:_MAX_CHARS]
        return trimmed


class ChatResponse(BaseModel):
    reply: str


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    settings = get_settings()
    if not settings.gemini_api_key:
        raise HTTPException(
            status_code=503,
            detail="Assistant not configured (no GEMINI_API_KEY on the server).",
        )

    location_hint = body.address or (
        f"{body.lat:.5f}, {body.lng:.5f}" if body.lat is not None and body.lng is not None else None
    )
    try:
        reply = await agent_reply(
            [m.model_dump() for m in body.messages],
            language=body.language,
            location_hint=location_hint,
        )
    except AgentError as exc:
        # Honest failure — never fabricate a reply.
        raise HTTPException(status_code=502, detail=f"Assistant unavailable: {exc}") from exc
    return ChatResponse(reply=reply)
