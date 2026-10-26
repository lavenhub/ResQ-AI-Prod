"""Resend transactional-email provider — real integration.

API contract (verified against Resend's official docs, Oct 2026):

  Send:
    POST https://api.resend.com/emails
    headers: {"Authorization": "Bearer re_...", "Content-Type": "application/json",
              "Idempotency-Key": <optional, <=256 chars, dedupes for 24h>}
    body: {"from": "Name <addr>", "to": ["a@b.com", ...], "subject": str,
           "html": str, "text": str, "reply_to": str|[str], "tags": [{name,value}]}
    success (200/201): {"id": "<uuid>"}   # the email id we store + correlate on

  Webhooks (delivery events), signed with Svix:
    headers: svix-id, svix-timestamp, svix-signature
    secret:  whsec_<base64>  (EMAIL_WEBHOOK_SECRET)
    signed_content = f"{svix-id}.{svix-timestamp}.{raw_body}"
    expected       = base64(HMAC_SHA256(base64decode(secret_after_prefix), signed_content))
    svix-signature = space-delimited "v1,<sig>" entries; a match on any passes.
    payload: {"type": "email.delivered"|..., "created_at": iso,
              "data": {"email_id": "<uuid>", "to": [...], "subject": str,
                       "bounce": {"type","subType","message"}, ...}}

We never fake a send and never claim delivery without a webhook saying so.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time

import httpx
from email.utils import formataddr
from loguru import logger

from ...config import get_settings
from ...models import NotificationStatus
from .base import DeliveryUpdate, EmailProvider, OutboundEmail, SendOutcome, SendResult

# Resend event type -> (internal status | None, is_bounce, is_complaint).
# We deliberately do NOT map email.opened / email.clicked: we make no claim that a
# recipient read the message, and we add no tracking of that kind.
_EVENT_MAP: dict[str, tuple[NotificationStatus | None, bool, bool]] = {
    "email.sent": (NotificationStatus.ACCEPTED_BY_PROVIDER, False, False),
    "email.delivered": (NotificationStatus.DELIVERED, False, False),
    "email.delivery_delayed": (NotificationStatus.DEFERRED, False, False),
    "email.bounced": (NotificationStatus.BOUNCED, True, False),
    "email.failed": (NotificationStatus.FAILED, False, False),
    "email.complained": (None, False, True),
}


class ResendEmailProvider(EmailProvider):
    name = "resend"
    channel = "email"

    def __init__(self) -> None:
        s = get_settings()
        self._api_key = s.email_api_key
        self._from_address = s.email_from_address
        self._from_name = s.email_from_name
        self._reply_to = s.email_reply_to
        self._timeout = s.email_provider_timeout_ms / 1000.0
        self._webhook_secret = s.email_webhook_secret
        self._tolerance = s.email_webhook_tolerance_seconds
        self._send_url = f"{s.email_api_base.rstrip('/')}/emails"

    # ── configuration ───────────────────────────────────────────────────────
    def validate_configuration(self) -> list[str]:
        # Only SEND-blocking problems belong here. A missing webhook secret does
        # not block sending — it only disables inbound delivery receipts — so it
        # is reported separately via ``delivery_receipts_configured``.
        problems: list[str] = []
        if not self._api_key:
            problems.append("EMAIL_API_KEY is not set")
        if not self._from_address:
            problems.append("EMAIL_FROM_ADDRESS is not set")
        return problems

    @property
    def delivery_receipts_configured(self) -> bool:
        """True when the webhook secret is set, so delivered/bounce events can be
        verified and applied. Independent of send readiness."""
        return bool(self._webhook_secret)

    # ── send ─────────────────────────────────────────────────────────────────
    async def send(self, msg: OutboundEmail) -> SendResult:
        # Missing credentials are a PERMANENT failure (retrying won't help).
        blocking = self.validate_configuration()
        if blocking:
            return SendResult(
                SendOutcome.PERMANENT, detail=f"provider not configured: {'; '.join(blocking)}"
            )
        if not msg.to_email:
            return SendResult(SendOutcome.PERMANENT, detail="no recipient email address")

        body: dict = {
            "from": formataddr((self._from_name, self._from_address)),
            "to": [msg.to_email],
            "subject": msg.subject,
            "text": msg.text,
        }
        if msg.html:
            body["html"] = msg.html
        if self._reply_to:
            body["reply_to"] = self._reply_to
        if msg.tags:
            # Resend tag values must be ASCII letters/digits/_/- ; callers pass safe values.
            body["tags"] = [{"name": k, "value": v} for k, v in msg.tags.items()]

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        if msg.idempotency_key:
            headers["Idempotency-Key"] = msg.idempotency_key[:256]

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await http.post(self._send_url, json=body, headers=headers)
        except httpx.TimeoutException:
            # Provider may or may not have accepted it — do NOT assume failure and
            # do NOT blindly retry (could double-send). Caller marks UNKNOWN.
            logger.warning("[resend] send timed out — outcome UNKNOWN")
            return SendResult(SendOutcome.UNKNOWN, detail="request timed out")
        except httpx.HTTPError as exc:  # connection errors etc. are retryable
            return SendResult(SendOutcome.RETRYABLE, detail=f"transport error: {exc}")

        return self._interpret_response(resp)

    def _interpret_response(self, resp: httpx.Response) -> SendResult:
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            secs = float(retry_after) if (retry_after or "").replace(".", "", 1).isdigit() else None
            return SendResult(SendOutcome.RETRYABLE, detail="rate limited", retry_after_seconds=secs)
        if resp.status_code >= 500:
            return SendResult(SendOutcome.RETRYABLE, detail=f"provider {resp.status_code}")

        try:
            data = resp.json()
        except ValueError:
            data = {}

        if 200 <= resp.status_code < 300:
            mid = (data or {}).get("id")
            if mid:
                return SendResult(SendOutcome.ACCEPTED, provider_message_id=str(mid))
            return SendResult(SendOutcome.UNKNOWN, detail="accepted but no id in response")

        # 4xx — a rejection. Auth (401/403) and invalid payload/recipient (422, 400)
        # are permanent; keep Resend's own message (not a secret) for the audit trail.
        msg = str((data or {}).get("message") or (data or {}).get("name") or f"HTTP {resp.status_code}")
        if resp.status_code in (400, 401, 403, 404, 422):
            return SendResult(SendOutcome.PERMANENT, detail=msg)
        return SendResult(SendOutcome.RETRYABLE, detail=msg)

    # ── webhook verification (Svix) ───────────────────────────────────────────
    def verify_webhook(self, *, raw_body: bytes, headers: dict[str, str]) -> bool:
        if not self._webhook_secret:
            logger.warning("[resend] webhook rejected: EMAIL_WEBHOOK_SECRET not configured")
            return False
        h = {k.lower(): v for k, v in headers.items()}
        svix_id = h.get("svix-id")
        svix_ts = h.get("svix-timestamp")
        svix_sig = h.get("svix-signature")
        if not (svix_id and svix_ts and svix_sig):
            return False

        # Replay guard: reject stale timestamps.
        try:
            ts = int(svix_ts)
        except ValueError:
            return False
        if abs(time.time() - ts) > self._tolerance:
            logger.warning("[resend] webhook rejected: timestamp outside tolerance")
            return False

        try:
            secret_bytes = base64.b64decode(self._webhook_secret.split("_", 1)[1])
        except (IndexError, ValueError, base64.binascii.Error):  # type: ignore[attr-defined]
            logger.warning("[resend] webhook secret is malformed (expected whsec_<base64>)")
            return False

        signed_content = b"%s.%s.%s" % (svix_id.encode(), svix_ts.encode(), raw_body)
        expected = base64.b64encode(
            hmac.new(secret_bytes, signed_content, hashlib.sha256).digest()
        ).decode()

        # svix-signature is a space-delimited list of "v1,<base64sig>" entries.
        for part in svix_sig.split(" "):
            _, _, sig = part.partition(",")
            if sig and hmac.compare_digest(sig, expected):
                return True
        logger.warning("[resend] webhook rejected: no matching signature")
        return False

    # ── webhook parsing ────────────────────────────────────────────────────────
    def parse_webhook_event(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        if not isinstance(payload, dict):
            return []
        event = str(payload.get("type", "")).strip().lower()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        mid = data.get("email_id") or data.get("id")
        if not mid or event not in _EVENT_MAP:
            return []  # unknown/ignored event (e.g. opened/clicked) — no status change

        status, is_bounce, is_complaint = _EVENT_MAP[event]
        detail: str | None = None
        bounce = data.get("bounce")
        if isinstance(bounce, dict):
            detail = bounce.get("message") or bounce.get("subType") or bounce.get("type")
        return [
            DeliveryUpdate(
                provider_message_id=str(mid),
                event_type=event,
                status=status,
                detail=str(detail) if detail else event,
                is_bounce=is_bounce,
                is_complaint=is_complaint,
            )
        ]
