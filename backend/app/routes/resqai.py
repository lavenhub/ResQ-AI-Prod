"""Hosted voice-agent (Tough Tongue AI) configuration passthrough.

The frontend `ResQAIWidget` can fetch the current hosted embed URL from here at
runtime, so the URL can be rotated without rebuilding the frontend.

This is configuration convenience, NOT a token-minting security boundary: it
returns only whatever `RESQAI_AGENT_URL` is configured on the server. Do not put
a long-lived provider API key here. `no-store` keeps a token-bearing URL out of
shared caches.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..config import get_settings

router = APIRouter(prefix="/api/resqai")

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/config")
async def config() -> JSONResponse:
    url = get_settings().resqai_agent_url
    if not url:
        return JSONResponse(
            status_code=503,
            headers=_NO_STORE,
            content={
                "configured": False,
                "message": "Hosted voice agent URL is not configured on the server.",
            },
        )
    return JSONResponse(headers=_NO_STORE, content={"configured": True, "agentUrl": url})
