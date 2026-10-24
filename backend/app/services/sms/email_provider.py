"""Email provider — the entirely-free, DLT-free emergency alert channel.

Sends via plain SMTP (stdlib ``smtplib``), so it works with ANY mailbox:
  * a Gmail account + an App Password (free, immediate), or
  * a free transactional tier such as Brevo (300 emails/day, forever free),
    which additionally supports a delivery webhook.

It implements the SAME ``SmsProvider`` interface as the carriers, so it plugs
into the existing outbox/worker/retry/status machinery unchanged. Honesty is
preserved end-to-end:
  * a successful SMTP handoff is ``ACCEPTED`` (the mail server took it) — NOT
    ``DELIVERED``; delivery only becomes true via a provider webhook.
  * we set our own ``Message-ID`` and store it as the provider message id, so a
    Brevo (or test) delivery webhook can be correlated back to the job.

SMTP is blocking, so callers run ``send_emergency_sms`` from a worker thread
(the notifier already does).
"""

from __future__ import annotations

import hmac
import smtplib
import ssl
import uuid
from email.message import EmailMessage
from email.utils import formataddr

from loguru import logger

from ...config import get_settings
from ...models import NotificationStatus
from .base import DeliveryUpdate, OutboundSms, SendOutcome, SendResult, SmsProvider

# Brevo (and common) event names -> internal status.
_EVENT_MAP: dict[str, NotificationStatus] = {
    "delivered": NotificationStatus.DELIVERED,
    "request": NotificationStatus.PROVIDER_ACCEPTED,
    "hard_bounce": NotificationStatus.FAILED,
    "soft_bounce": NotificationStatus.FAILED,
    "blocked": NotificationStatus.FAILED,
    "invalid_email": NotificationStatus.FAILED,
    "error": NotificationStatus.FAILED,
    "deferred": NotificationStatus.UNKNOWN,
    # Also accept our own simple test shape.
    "failed": NotificationStatus.FAILED,
    "unknown": NotificationStatus.UNKNOWN,
}


class EmailSmsProvider(SmsProvider):
    name = "email"
    channel = "email"

    def __init__(self) -> None:
        s = get_settings()
        self._host = s.smtp_host
        self._port = s.smtp_port
        self._username = s.smtp_username
        self._password = s.smtp_password
        self._from = s.smtp_from or s.smtp_username
        self._from_name = s.smtp_from_name
        self._use_ssl = s.smtp_use_ssl
        self._use_tls = s.smtp_use_tls
        self._timeout = s.sms_provider_timeout_ms / 1000.0
        self._callback_secret = s.sms_callback_secret

    def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        if not self._host:
            problems.append("SMTP_HOST is not set")
        if not self._username:
            problems.append("SMTP_USERNAME is not set")
        if not self._password:
            problems.append("SMTP_PASSWORD is not set")
        if not self._from:
            problems.append("SMTP_FROM (or SMTP_USERNAME) is not set")
        return problems

    async def send_emergency_sms(self, msg: OutboundSms) -> SendResult:
        blocking = self.validate_configuration()
        if blocking:
            return SendResult(
                SendOutcome.PERMANENT, detail=f"email not configured: {'; '.join(blocking)}"
            )
        if not msg.to_e164:
            # No email address on file for this contact.
            return SendResult(SendOutcome.PERMANENT, detail="no email address for recipient")

        # A stable Message-ID we control, so a delivery webhook can be matched back.
        message_id = f"{uuid.uuid4().hex}@resqai"
        email = EmailMessage()
        email["From"] = formataddr((self._from_name, self._from))
        email["To"] = msg.to_e164
        email["Subject"] = msg.subject or "ResQAI emergency alert"
        email["Message-ID"] = f"<{message_id}>"
        email.set_content(msg.body)
        # variables may carry a prebuilt HTML part.
        html = msg.variables.get("_html")
        if html:
            email.add_alternative(html, subtype="html")

        try:
            self._smtp_send(email)
        except (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused) as exc:
            # Credentials / sender rejected — retrying won't help.
            return SendResult(SendOutcome.PERMANENT, detail=f"smtp auth/sender error: {exc}")
        except smtplib.SMTPRecipientsRefused as exc:
            return SendResult(SendOutcome.PERMANENT, detail=f"recipient refused: {exc}")
        except (smtplib.SMTPException, OSError, ssl.SSLError) as exc:
            # Transient network / server issue — safe to back off and retry.
            return SendResult(SendOutcome.RETRYABLE, detail=f"smtp transport error: {exc}")

        logger.info(f"[email] accepted by SMTP server id={message_id} -> {msg.to_e164}")
        return SendResult(SendOutcome.ACCEPTED, provider_message_id=message_id)

    def _smtp_send(self, email: EmailMessage) -> None:
        context = ssl.create_default_context()
        if self._use_ssl:
            with smtplib.SMTP_SSL(self._host, self._port, timeout=self._timeout, context=context) as server:
                server.login(self._username, self._password)
                server.send_message(email)
        else:
            with smtplib.SMTP(self._host, self._port, timeout=self._timeout) as server:
                server.ehlo()
                if self._use_tls:
                    server.starttls(context=context)
                    server.ehlo()
                server.login(self._username, self._password)
                server.send_message(email)

    def parse_delivery_callback(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Parse a Brevo-style event webhook (single or list), or our simple test
        shape ``{message_id, status}``."""
        items: list[dict]
        if isinstance(payload, list):
            items = [p for p in payload if isinstance(p, dict)]
        elif isinstance(payload.get("items"), list):
            items = [p for p in payload["items"] if isinstance(p, dict)]
        elif isinstance(payload.get("data"), list):
            items = [p for p in payload["data"] if isinstance(p, dict)]
        else:
            items = [payload]

        updates: list[DeliveryUpdate] = []
        for item in items:
            raw_mid = (
                item.get("message-id")
                or item.get("message_id")
                or item.get("messageId")
                or item.get("id")
            )
            if not raw_mid:
                continue
            mid = str(raw_mid).strip().strip("<>")
            event = str(item.get("event") or item.get("status") or "").strip().lower()
            status = _EVENT_MAP.get(event, NotificationStatus.UNKNOWN)
            updates.append(
                DeliveryUpdate(
                    provider_message_id=mid,
                    status=status,
                    detail=item.get("reason") or event or None,
                )
            )
        return updates

    def verify_callback(self, *, secret: str | None) -> bool:
        if not self._callback_secret:
            logger.warning("[email] callback rejected: SMS_CALLBACK_SECRET not configured")
            return False
        return bool(secret) and hmac.compare_digest(secret, self._callback_secret)
