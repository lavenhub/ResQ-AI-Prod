"""Mock email provider — FOR DEVELOPMENT AND TESTS ONLY.

Never sends a real email and must never be used to claim a real email was
delivered. It records each "send" in an in-process log so tests and local demos
can assert what *would* have been sent, and returns ``ACCEPTED`` (provider took
the request) — NOT ``DELIVERED``. Delivery only ever becomes true when a webhook
event is processed, exactly as with the real provider.

Failure injection (for tests): the recipient's local-part prefix forces an
outcome, so retry/backoff/permanent-failure paths can be exercised without
patching internals:
    permanent+...@   -> PERMANENT failure
    retryable+...@   -> RETRYABLE failure
    timeout+...@     -> UNKNOWN (timeout-like) outcome
"""

from __future__ import annotations

import hmac
import uuid
from dataclasses import dataclass

from loguru import logger

from ...config import get_settings
from ...models import NotificationStatus
from .base import DeliveryUpdate, EmailProvider, OutboundEmail, SendOutcome, SendResult

_EVENT_MAP: dict[str, NotificationStatus] = {
    "delivered": NotificationStatus.DELIVERED,
    "email.delivered": NotificationStatus.DELIVERED,
    "bounced": NotificationStatus.BOUNCED,
    "email.bounced": NotificationStatus.BOUNCED,
    "deferred": NotificationStatus.DEFERRED,
    "email.delivery_delayed": NotificationStatus.DEFERRED,
    "failed": NotificationStatus.FAILED,
    "email.failed": NotificationStatus.FAILED,
    "sent": NotificationStatus.ACCEPTED_BY_PROVIDER,
    "email.sent": NotificationStatus.ACCEPTED_BY_PROVIDER,
}


@dataclass
class _Sent:
    to: str
    subject: str
    body: str
    provider_message_id: str
    outcome: SendOutcome


class MockEmailProvider(EmailProvider):
    name = "mock"
    channel = "email"

    def __init__(self, webhook_secret: str | None = None) -> None:
        self._webhook_secret = webhook_secret
        self.sent: list[_Sent] = []

    @property
    def is_mock(self) -> bool:
        return True

    async def send(self, msg: OutboundEmail) -> SendResult:
        local = msg.to_email.split("@", 1)[0].lower()
        if local.startswith("permanent"):
            logger.info(f"[mock-email] PERMANENT-fail (test) -> {msg.to_email}")
            return SendResult(SendOutcome.PERMANENT, detail="mock permanent failure")
        if local.startswith("retryable"):
            logger.info(f"[mock-email] RETRYABLE-fail (test) -> {msg.to_email}")
            return SendResult(SendOutcome.RETRYABLE, detail="mock transient failure")
        if local.startswith("timeout"):
            logger.info(f"[mock-email] UNKNOWN (test) -> {msg.to_email}")
            return SendResult(SendOutcome.UNKNOWN, detail="mock timeout")

        mid = f"mock-{uuid.uuid4().hex}"
        self.sent.append(_Sent(msg.to_email, msg.subject, msg.text, mid, SendOutcome.ACCEPTED))
        logger.info(f"[mock-email] ACCEPTED (no real email sent) id={mid} -> {msg.to_email}")
        return SendResult(SendOutcome.ACCEPTED, provider_message_id=mid)

    def parse_webhook_event(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Accepts the Resend shape ``{type, data:{email_id}}`` or a simple
        ``{email_id|message_id, status|event}`` so tests can simulate events."""
        if not isinstance(payload, dict):
            return []
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        mid = data.get("email_id") or data.get("message_id") or data.get("id")
        if not mid:
            return []
        event = str(payload.get("type") or data.get("event") or data.get("status") or "").lower()
        status = _EVENT_MAP.get(event, NotificationStatus.UNKNOWN)
        return [
            DeliveryUpdate(
                provider_message_id=str(mid),
                event_type=event or "mock",
                status=status,
                detail=event or None,
                is_bounce=status == NotificationStatus.BOUNCED,
                is_complaint=event in ("complained", "email.complained"),
            )
        ]

    def verify_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        # No secret configured => accept (local dev convenience). With a secret,
        # require a matching plaintext token in the x-webhook-secret header.
        if not self._webhook_secret:
            return True
        h = {k.lower(): v for k, v in headers.items()}
        presented = h.get("x-webhook-secret", "")
        return bool(presented) and hmac.compare_digest(presented, self._webhook_secret)

    def validate_configuration(self) -> list[str]:
        # Always "ready" — but it is a mock, which the health endpoint surfaces
        # separately so no one mistakes it for live delivery.
        return []


def _new_mock() -> MockEmailProvider:
    return MockEmailProvider(webhook_secret=get_settings().email_webhook_secret or None)
