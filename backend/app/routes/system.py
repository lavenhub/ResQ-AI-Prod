"""Health + status endpoints the frontend polls."""

from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import get_settings
from ..services.email import get_provider

router = APIRouter()


@router.get("/health")
async def health() -> dict:
    """Liveness probe. NEVER sends an email — config/readiness only."""
    return {"status": "ok", "version": __version__}


@router.get("/api/email/health")
async def email_health() -> dict:
    """Report whether the email provider is configured for REAL sending.

    This NEVER sends an email — it only checks configuration. ``ready`` is true
    only when a real provider is fully configured; the mock provider reports
    ``provider: "mock"`` so no one mistakes it for live delivery.
    """
    provider = get_provider()
    problems = provider.validate_configuration()
    is_mock = provider.is_mock
    return {
        "provider": provider.name,
        "channel": provider.channel,
        "ready": (not problems) and (not is_mock),
        "isMock": is_mock,
        "problems": problems,
        "realSendingConfigured": (not is_mock) and (not problems),
    }


@router.get("/api/status")
async def status() -> dict:
    """The frontend reads `hasApiKey` to know whether real voice is available.

    We report the TRUE state: voice is only enabled when an AI provider key is
    configured. No key => the UI should surface that voice is unavailable rather
    than imply a working call.
    """
    settings = get_settings()
    return {
        "hasApiKey": settings.voice_enabled,
        "voiceProvider": "gemini-live" if settings.voice_enabled else None,
        "version": __version__,
    }
