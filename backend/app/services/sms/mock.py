"""Mock SMS provider — FOR DEVELOPMENT AND TESTS ONLY.

This never contacts a real carrier and must never be used to claim a real SMS was
delivered. It records each "send" in an in-process log so tests and local demos
can assert what *would* have been sent, and returns ``ACCEPTED`` (provider took
the request) — NOT ``DELIVERED``. Delivery only ever becomes true when a delivery
callback is processed, exactly as with a real provider; the mock does not fake a
DLR on its own.

Failure injection (for tests): a recipient number containing the magic markers
below forces a specific outcome, so retry/backoff/permanent-failure paths can be
exercised deterministically without patching internals.
    ...0000  -> PERMANENT failure
    ...9999  -> RETRYABLE failure
    ...7777  -> UNKNOWN (timeout-like) outcome
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from loguru import logger

from ...models import NotificationStatus
from .base import DeliveryUpdate, OutboundSms, SendOutcome, SendResult, SmsProvider


@dataclass
class _Sent:
    to: str
    body: str
    provider_message_id: str
    outcome: SendOutcome


class MockSmsProvider(SmsProvider):
    name = "mock"

    def __init__(self, callback_secret: str | None = None) -> None:
        self._callback_secret = callback_secret
        self.sent: list[_Sent] = []

    async def send_emergency_sms(self, msg: OutboundSms) -> SendResult:
        tail = msg.to_e164[-4:]
        if tail == "0000":
            logger.info(f"[mock-sms] PERMANENT-fail (test) -> {msg.to_e164}")
            return SendResult(SendOutcome.PERMANENT, detail="mock permanent failure")
        if tail == "9999":
            logger.info(f"[mock-sms] RETRYABLE-fail (test) -> {msg.to_e164}")
            return SendResult(SendOutcome.RETRYABLE, detail="mock transient failure")
        if tail == "7777":
            logger.info(f"[mock-sms] UNKNOWN (test) -> {msg.to_e164}")
            return SendResult(SendOutcome.UNKNOWN, detail="mock timeout")

        mid = f"mock-{uuid.uuid4().hex[:16]}"
        self.sent.append(_Sent(msg.to_e164, msg.body, mid, SendOutcome.ACCEPTED))
        logger.info(f"[mock-sms] ACCEPTED (no real SMS sent) id={mid} -> {msg.to_e164}")
        return SendResult(SendOutcome.ACCEPTED, provider_message_id=mid)

    def parse_delivery_callback(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Accepts a simple ``{message_id, status}`` (or ``requestId``) shape so
        tests and local demos can simulate carrier DLRs."""
        mid = payload.get("message_id") or payload.get("requestId") or payload.get("id")
        raw = str(payload.get("status", "")).lower()
        if not mid:
            return []
        status_map = {
            "delivered": NotificationStatus.DELIVERED,
            "failed": NotificationStatus.FAILED,
            "undelivered": NotificationStatus.FAILED,
            "unknown": NotificationStatus.UNKNOWN,
        }
        status = status_map.get(raw, NotificationStatus.UNKNOWN)
        return [DeliveryUpdate(provider_message_id=str(mid), status=status, detail=raw or None)]

    def verify_callback(self, *, secret: str | None) -> bool:
        # If no secret configured for the mock, accept (local dev convenience).
        if not self._callback_secret:
            return True
        import hmac

        return bool(secret) and hmac.compare_digest(secret, self._callback_secret)

    def validate_configuration(self) -> list[str]:
        # Always "ready" — but it is a mock, which is itself a limitation the
        # health endpoint surfaces separately.
        return []
