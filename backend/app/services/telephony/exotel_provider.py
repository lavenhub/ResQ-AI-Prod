"""Exotel provider — India-native outbound calls (adapter; demo-UNVERIFIED).

Shipped so the provider abstraction is proven with a second real provider and so
a production India-domestic rollout (proper Indian caller-ID + DLT compliance)
can switch to Exotel without touching the pipeline or state machine. It is NOT
the verified demo path — Exotel requires a KYC'd business account and a
bidirectional streaming (Voicebot) applet, so ``validate_configuration`` flags
what still needs wiring. Pipecat's ``ExotelFrameSerializer`` handles the media.

Env (reuses the generic telephony vars):
  TELEPHONY_ACCOUNT_SID  -> Exotel account SID / subdomain SID
  TELEPHONY_API_KEY      -> Exotel API key   (alias filled into account_sid if SID unset)
  TELEPHONY_API_SECRET   -> Exotel API token (alias -> auth_token)
  TELEPHONY_CALLER_ID    -> a verified ExoPhone
  EXOTEL_SUBDOMAIN       -> api.exotel.com (default) or api.in.exotel.com
"""

from __future__ import annotations

import os

import httpx
from loguru import logger

from ...config import get_settings
from ...models import CallStatus
from .base import (
    AnswerDocument,
    CallRequest,
    CallResult,
    CallStatusUpdate,
    TelephonyProvider,
)

_HTTP_TIMEOUT_S = 15.0

_EXOTEL_STATUS_MAP = {
    "queued": CallStatus.INITIATING,
    "in-progress": CallStatus.CONNECTED,
    "ringing": CallStatus.RINGING,
    "completed": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.NO_ANSWER,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELLED,
}


def _map_status(raw: str | None) -> CallStatus | None:
    if not raw:
        return None
    return _EXOTEL_STATUS_MAP.get(raw.strip().lower())


class ExotelTelephonyProvider(TelephonyProvider):
    name = "exotel"

    def __init__(self) -> None:
        s = get_settings()
        self._sid = s.telephony_account_sid
        self._token = s.telephony_auth_token
        self._caller_id = s.telephony_caller_id
        self._subdomain = os.environ.get("EXOTEL_SUBDOMAIN", "api.exotel.com").strip()

    def _root(self) -> str:
        return f"https://{self._subdomain}/v1/Accounts/{self._sid}"

    async def initiate_outbound_call(self, req: CallRequest) -> CallResult:
        if problems := self.validate_configuration():
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail="Exotel not configured: " + "; ".join(problems),
            )
        url = f"{self._root()}/Calls/connect.json"
        data = {
            "From": req.to_number,            # Exotel connects From->To; we call the reception
            "CallerId": req.from_number or self._caller_id,
            "Url": req.answer_url,            # Exotel applet (streaming) URL
            "StatusCallback": req.status_callback_url,
            "TimeLimit": str(max(30, req.max_seconds)),
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(url, data=data, auth=(self._sid, self._token))
        except httpx.HTTPError as exc:
            logger.warning(f"[exotel] network error placing call: {exc}")
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail=f"network error: {str(exc)[:200]}",
            )
        if resp.status_code >= 400:
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail=f"provider rejected ({resp.status_code})",
            )
        body = resp.json()
        call = (body.get("Call") or {}) if isinstance(body, dict) else {}
        return CallResult(
            ok=True, provider=self.name,
            provider_call_id=call.get("Sid"),
            status=_map_status(call.get("Status")) or CallStatus.INITIATING,
        )

    async def get_call_status(self, provider_call_id: str) -> CallStatusUpdate | None:
        url = f"{self._root()}/Calls/{provider_call_id}.json"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, auth=(self._sid, self._token))
        except httpx.HTTPError:
            return None
        if resp.status_code >= 400:
            return None
        call = (resp.json().get("Call") or {})
        status = _map_status(call.get("Status"))
        return (
            CallStatusUpdate(provider_call_id=provider_call_id, status=status,
                             raw_status=call.get("Status"))
            if status else None
        )

    async def terminate_call(self, provider_call_id: str) -> bool:
        url = f"{self._root()}/Calls/{provider_call_id}.json"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(url, data={"Status": "completed"},
                                         auth=(self._sid, self._token))
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    def verify_webhook(
        self, *, url: str, raw_body: bytes, form: dict[str, str], headers: dict[str, str]
    ) -> bool:
        # Exotel does not HMAC-sign like Twilio; authenticate via a shared secret
        # token configured on the Exotel callback URL and matched here.
        secret = get_settings().telephony_webhook_secret
        if not secret:
            return False
        provided = (
            headers.get("x-telephony-signature")
            or form.get("token")
            or form.get("secret")
        )
        return bool(provided) and provided == secret

    def parse_status_webhook(self, form: dict[str, str]) -> CallStatusUpdate | None:
        call_id = form.get("CallSid") or form.get("CallSid".lower())
        raw = form.get("Status") or form.get("CallStatus")
        status = _map_status(raw)
        if not call_id or status is None:
            return None
        return CallStatusUpdate(provider_call_id=call_id, status=status, raw_status=raw)

    def build_call_answer_document(
        self, *, media_ws_url: str, booking_id: str
    ) -> AnswerDocument:
        # Exotel's streaming applet is configured in the Exotel dashboard, not via
        # TwiML. We still return the media URL for completeness/documentation.
        return AnswerDocument(body=media_ws_url, media_type="text/plain")

    def build_media_serializer(self, start_payload: dict):
        from pipecat.serializers.exotel import ExotelFrameSerializer

        start = start_payload.get("start", {}) if isinstance(start_payload, dict) else {}
        stream_sid = start.get("stream_sid") or start_payload.get("stream_sid") or start.get("streamSid")
        call_sid = start.get("call_sid") or start.get("callSid")
        return ExotelFrameSerializer(stream_sid=stream_sid, call_sid=call_sid)

    def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        if not self._sid:
            problems.append("TELEPHONY_ACCOUNT_SID (Exotel SID) is not set")
        if not self._token:
            problems.append("TELEPHONY_AUTH_TOKEN (Exotel API token) is not set")
        if not self._caller_id:
            problems.append("TELEPHONY_CALLER_ID (a verified ExoPhone) is not set")
        problems.append(
            "Exotel is a demo-UNVERIFIED adapter: it also requires a KYC'd Exotel "
            "account and a bidirectional streaming (Voicebot) applet configured in "
            "the Exotel dashboard. Use TELEPHONY_PROVIDER=twilio for the demo."
        )
        return problems
