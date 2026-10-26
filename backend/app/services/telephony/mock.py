"""Mock telephony provider — dev/tests ONLY.

Simulates a call lifecycle entirely in memory and NEVER places a real PSTN call.
It is the default provider (``TELEPHONY_PROVIDER=mock``) so tests and offline
demos exercise the whole booking + state-machine flow without dialing anyone.

Status callbacks in tests are POSTed to the status webhook using the SAME
Twilio-style field names the real provider uses (``CallSid`` + ``CallStatus``),
so the parsing path under test is the real one. Webhook authenticity is checked
against ``TELEPHONY_WEBHOOK_SECRET`` via an ``x-telephony-signature`` header when a
secret is configured, letting tests assert that forged/unsigned webhooks are
rejected.
"""

from __future__ import annotations

import uuid

from ...config import get_settings
from ...models import CallStatus
from .base import (
    AnswerDocument,
    CallRequest,
    CallResult,
    CallStatusUpdate,
    TelephonyProvider,
)
from .twilio_provider import map_twilio_status


class MockTelephonyProvider(TelephonyProvider):
    name = "mock"

    def __init__(self) -> None:
        # provider_call_id -> last known CallStatus (for get_call_status/terminate).
        self.calls: dict[str, CallStatus] = {}
        # Record of placed calls so tests can assert what we "dialed".
        self.placed: list[CallRequest] = []

    async def initiate_outbound_call(self, req: CallRequest) -> CallResult:
        call_id = f"MOCK-{uuid.uuid4().hex[:16]}"
        self.calls[call_id] = CallStatus.INITIATING
        self.placed.append(req)
        return CallResult(
            ok=True,
            provider=self.name,
            provider_call_id=call_id,
            status=CallStatus.INITIATING,
            detail="mock call accepted (no real PSTN call placed)",
        )

    async def get_call_status(self, provider_call_id: str) -> CallStatusUpdate | None:
        status = self.calls.get(provider_call_id)
        if status is None:
            return None
        return CallStatusUpdate(provider_call_id=provider_call_id, status=status)

    async def terminate_call(self, provider_call_id: str) -> bool:
        if provider_call_id in self.calls:
            self.calls[provider_call_id] = CallStatus.CANCELLED
            return True
        return False

    def verify_webhook(
        self, *, url: str, raw_body: bytes, form: dict[str, str], headers: dict[str, str]
    ) -> bool:
        secret = get_settings().telephony_webhook_secret
        if not secret:
            # No secret configured => accept (dev convenience). The route still
            # logs a warning elsewhere; real providers set a secret/signature.
            return True
        provided = headers.get("x-telephony-signature") or headers.get("X-Telephony-Signature")
        return bool(provided) and provided == secret

    def parse_status_webhook(self, form: dict[str, str]) -> CallStatusUpdate | None:
        call_id = form.get("CallSid") or form.get("provider_call_id")
        raw = form.get("CallStatus") or form.get("status")
        if not call_id or not raw:
            return None
        status = map_twilio_status(raw)
        if status is None:
            return None
        # Keep our in-memory view consistent so a later poll agrees.
        self.calls[call_id] = status
        return CallStatusUpdate(
            provider_call_id=call_id, status=status, raw_status=raw
        )

    def build_call_answer_document(
        self, *, media_ws_url: str, booking_id: str
    ) -> AnswerDocument:
        # Mirrors the real TwiML shape so the document path is exercised, but it
        # is never fetched by a real provider in mock mode.
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            "<Response><Connect>"
            f'<Stream url="{media_ws_url}"/>'
            "</Connect></Response>"
        )
        return AnswerDocument(body=body, media_type="application/xml")

    def build_media_serializer(self, start_payload: dict):
        # The mock does not bridge real media; the media WebSocket is never used
        # in mock mode. Fail loudly if something tries to.
        raise NotImplementedError("MockTelephonyProvider does not stream real media")

    def validate_configuration(self) -> list[str]:
        # The mock is always "ready" to simulate — but it is a simulation.
        return []

    @property
    def is_mock(self) -> bool:
        return True


def _new_mock() -> MockTelephonyProvider:
    return MockTelephonyProvider()
