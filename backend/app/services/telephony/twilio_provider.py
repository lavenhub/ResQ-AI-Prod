"""Twilio Programmable Voice provider — REAL outbound PSTN calls.

Uses Twilio's REST API directly over httpx (already a dependency) so we add no
new SDK, and Pipecat's bundled ``TwilioFrameSerializer`` for the real-time media
bridge. The flow for one outbound call:

  1. ``initiate_outbound_call`` POSTs to the Calls resource with ``To`` (the
     hospital/ambulance reception number), ``From`` (our caller ID), ``Url`` (the
     public TwiML endpoint Twilio fetches on answer) and ``StatusCallback``.
  2. When the receptionist answers, Twilio fetches the TwiML from our
     ``build_call_answer_document`` which is ``<Connect><Stream>`` pointing at our
     media WebSocket — establishing a BIDIRECTIONAL audio stream.
  3. Twilio opens the WebSocket and sends a ``start`` event; we build a
     ``TwilioFrameSerializer`` from it and run the Gemini Live pipeline.
  4. Twilio POSTs call-status callbacks (ringing / answered / completed / busy /
     no-answer / failed), each verified via the ``X-Twilio-Signature`` header.

Credentials (Account SID + Auth Token) are SERVER-ONLY and never reach the
frontend. Nothing here is imported by client code.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

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

_TWILIO_API_ROOT = "https://api.twilio.com/2010-04-01"
_HTTP_TIMEOUT_S = 15.0


# Twilio call-status strings -> our internal CallStatus. Shared with the mock so
# the parsing path under test is the real one.
_TWILIO_STATUS_MAP = {
    "queued": CallStatus.INITIATING,
    "initiated": CallStatus.INITIATING,
    "ringing": CallStatus.RINGING,
    "in-progress": CallStatus.CONNECTED,
    "answered": CallStatus.CONNECTED,
    "completed": CallStatus.COMPLETED,
    "busy": CallStatus.BUSY,
    "no-answer": CallStatus.NO_ANSWER,
    "failed": CallStatus.FAILED,
    "canceled": CallStatus.CANCELLED,
    "cancelled": CallStatus.CANCELLED,
}


def map_twilio_status(raw: str | None) -> CallStatus | None:
    if not raw:
        return None
    return _TWILIO_STATUS_MAP.get(raw.strip().lower())


def _sanitize(detail: str | None) -> str | None:
    if not detail:
        return None
    return str(detail)[:300]


class TwilioTelephonyProvider(TelephonyProvider):
    name = "twilio"

    def __init__(self) -> None:
        s = get_settings()
        self._account_sid = s.telephony_account_sid
        self._auth_token = s.telephony_auth_token
        self._caller_id = s.telephony_caller_id

    # ── placing / controlling calls ─────────────────────────────────────────
    async def initiate_outbound_call(self, req: CallRequest) -> CallResult:
        if problems := self.validate_configuration():
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail="Twilio not configured: " + "; ".join(problems),
            )
        url = f"{_TWILIO_API_ROOT}/Accounts/{self._account_sid}/Calls.json"
        data = {
            "To": req.to_number,
            "From": req.from_number or self._caller_id,
            "Url": req.answer_url,
            "Method": "POST",
            "StatusCallback": req.status_callback_url,
            "StatusCallbackMethod": "POST",
            # Fire a callback at each lifecycle stage so our state machine tracks it.
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "TimeLimit": str(max(30, req.max_seconds)),
            # If nobody answers within this window, Twilio reports no-answer.
            "Timeout": "30",
        }
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(
                    url, data=data, auth=(self._account_sid, self._auth_token)
                )
        except httpx.HTTPError as exc:
            logger.warning(f"[twilio] network error placing call: {exc}")
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail=f"network error: {_sanitize(str(exc))}",
            )
        if resp.status_code >= 400:
            logger.warning(f"[twilio] call create failed {resp.status_code}: {resp.text[:300]}")
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail=f"provider rejected ({resp.status_code}): {_sanitize(resp.text)}",
            )
        body = resp.json()
        return CallResult(
            ok=True,
            provider=self.name,
            provider_call_id=body.get("sid"),
            status=map_twilio_status(body.get("status")) or CallStatus.INITIATING,
        )

    async def get_call_status(self, provider_call_id: str) -> CallStatusUpdate | None:
        url = f"{_TWILIO_API_ROOT}/Accounts/{self._account_sid}/Calls/{provider_call_id}.json"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.get(url, auth=(self._account_sid, self._auth_token))
        except httpx.HTTPError as exc:
            logger.warning(f"[twilio] status poll error: {exc}")
            return None
        if resp.status_code >= 400:
            return None
        body = resp.json()
        status = map_twilio_status(body.get("status"))
        if status is None:
            return None
        return CallStatusUpdate(
            provider_call_id=provider_call_id, status=status, raw_status=body.get("status")
        )

    async def terminate_call(self, provider_call_id: str) -> bool:
        url = f"{_TWILIO_API_ROOT}/Accounts/{self._account_sid}/Calls/{provider_call_id}.json"
        try:
            async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as client:
                resp = await client.post(
                    url, data={"Status": "completed"},
                    auth=(self._account_sid, self._auth_token),
                )
        except httpx.HTTPError:
            return False
        return resp.status_code < 400

    # ── webhooks ──────────────────────────────────────────────────────────────
    def verify_webhook(
        self, *, url: str, raw_body: bytes, form: dict[str, str], headers: dict[str, str]
    ) -> bool:
        """Verify Twilio's ``X-Twilio-Signature`` (HMAC-SHA1 over URL + sorted
        POST params, base64-encoded) — the documented Twilio request-validation
        algorithm, implemented with stdlib so no SDK is required."""
        if not self._auth_token:
            return False
        provided = headers.get("x-twilio-signature") or headers.get("X-Twilio-Signature")
        if not provided:
            return False
        s = url
        for key in sorted(form.keys()):
            s += key + (form[key] or "")
        digest = hmac.new(self._auth_token.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).digest()
        expected = base64.b64encode(digest).decode("ascii")
        return hmac.compare_digest(provided, expected)

    def parse_status_webhook(self, form: dict[str, str]) -> CallStatusUpdate | None:
        call_id = form.get("CallSid")
        raw = form.get("CallStatus")
        status = map_twilio_status(raw)
        if not call_id or status is None:
            return None
        return CallStatusUpdate(provider_call_id=call_id, status=status, raw_status=raw)

    # ── media bridge ────────────────────────────────────────────────────────
    def build_call_answer_document(
        self, *, media_ws_url: str, booking_id: str
    ) -> AnswerDocument:
        # <Connect><Stream> = BIDIRECTIONAL media stream. The call stays connected
        # to our WebSocket (where the AI listens + speaks) until the stream ends.
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response>"
            "<Connect>"
            f'<Stream url="{media_ws_url}">'
            f'<Parameter name="booking_id" value="{booking_id}"/>'
            "</Stream>"
            "</Connect>"
            "</Response>"
        )
        return AnswerDocument(body=body, media_type="application/xml")

    def build_media_serializer(self, start_payload: dict):
        # Lazy import keeps the Pipecat telephony dep out of the import path for
        # mock-mode tests.
        from pipecat.serializers.twilio import TwilioFrameSerializer

        start = start_payload.get("start", {}) if isinstance(start_payload, dict) else {}
        stream_sid = start.get("streamSid") or start_payload.get("streamSid")
        call_sid = start.get("callSid")
        return TwilioFrameSerializer(
            stream_sid=stream_sid,
            call_sid=call_sid,
            account_sid=self._account_sid,
            auth_token=self._auth_token,
        )

    # ── config ────────────────────────────────────────────────────────────────
    def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        if not self._account_sid:
            problems.append("TELEPHONY_ACCOUNT_SID is not set")
        if not self._auth_token:
            problems.append("TELEPHONY_AUTH_TOKEN is not set")
        if not self._caller_id:
            problems.append("TELEPHONY_CALLER_ID is not set")
        base = get_settings().telephony_base_url
        if not base.startswith("https://"):
            problems.append(
                "TELEPHONY_BASE_URL must be a public https URL so Twilio can reach "
                "the TwiML / status / media endpoints (use Render or an ngrok tunnel)"
            )
        return problems
