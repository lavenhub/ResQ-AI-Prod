"""MSG91 SMS provider — real India carrier integration (v5 Flow API).

API contract (verified against MSG91 official docs, Oct 2026):
  Send:
    POST {base}/api/v5/flow/
    headers: {"authkey": <MSG91_AUTH_KEY>, "Content-Type": "application/json"}
    body: {
        "flow_id": <DLT-approved template/flow id>,
        "sender":  <approved 6-char DLT header, e.g. "RESQAI">,
        "recipients": [ {"mobiles": "919876543210", <VAR>: <value>, ...} ]
    }
    success: {"type": "success", "message": "<requestId>"}
    error:   {"type": "error",   "message": "<reason>"}

  Delivery report (DLR) webhook (operator -> us), configured in the MSG91 panel:
    JSON body with fields incl. "requestId", "telNum", "status",
    "deliveryTime", "failureReason". MSG91 SMS DLR is NOT HMAC-signed, so we
    authenticate the webhook with a shared secret presented in the callback URL
    (see verify_callback) and compare it in constant time.

India/DLT: ``flow_id`` + ``sender`` MUST be DLT-registered and template-approved,
and the message content must match the approved template. Without that, MSG91
rejects the send (a PERMANENT outcome here) — we never fake success.
"""

from __future__ import annotations

import hmac

import httpx
from loguru import logger

from ...config import get_settings
from ...models import NotificationStatus
from .base import DeliveryUpdate, OutboundSms, SendOutcome, SendResult, SmsProvider

# MSG91 numeric/string status -> internal. Based on MSG91's documented DLR codes.
_STATUS_MAP: dict[str, NotificationStatus] = {
    "delivered": NotificationStatus.DELIVERED,
    "1": NotificationStatus.DELIVERED,
    "delivery_success": NotificationStatus.DELIVERED,
    "failed": NotificationStatus.FAILED,
    "2": NotificationStatus.FAILED,
    "ndnc": NotificationStatus.FAILED,
    "rejected": NotificationStatus.FAILED,
    "blocked": NotificationStatus.FAILED,
    "undelivered": NotificationStatus.FAILED,
    "expired": NotificationStatus.FAILED,
    "16": NotificationStatus.FAILED,
    "17": NotificationStatus.FAILED,
    "auto_failed": NotificationStatus.FAILED,
}


class Msg91SmsProvider(SmsProvider):
    name = "msg91"

    def __init__(self) -> None:
        s = get_settings()
        self._auth_key = s.msg91_auth_key
        self._flow_id = s.msg91_template_id
        self._sender = s.msg91_sender_id
        self._base_url = s.msg91_base_url.rstrip("/")
        self._timeout = s.sms_provider_timeout_ms / 1000.0
        self._callback_secret = s.sms_callback_secret

    def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        if not self._auth_key:
            problems.append("MSG91_AUTH_KEY is not set")
        if not self._flow_id:
            problems.append("MSG91_TEMPLATE_ID (flow_id) is not set")
        if not self._sender:
            problems.append("MSG91_SENDER_ID is not set")
        if not self._callback_secret:
            problems.append(
                "SMS_CALLBACK_SECRET is not set (delivery callbacks would be rejected)"
            )
        return problems

    async def send_emergency_sms(self, msg: OutboundSms) -> SendResult:
        cfg_problems = self.validate_configuration()
        # A missing callback secret must not block sending; only credential gaps do.
        blocking = [p for p in cfg_problems if "SMS_CALLBACK_SECRET" not in p]
        if blocking:
            return SendResult(
                SendOutcome.PERMANENT,
                detail=f"provider not configured: {'; '.join(blocking)}",
            )

        recipient: dict[str, str] = {"mobiles": msg.to_e164.lstrip("+")}
        # Pass template variables through as-is; the keys must match the approved
        # DLT template's variable names in the MSG91 panel.
        recipient.update({k: str(v) for k, v in msg.variables.items()})
        payload = {
            "flow_id": self._flow_id,
            "sender": self._sender,
            "recipients": [recipient],
        }
        headers = {"authkey": self._auth_key, "Content-Type": "application/json"}
        url = f"{self._base_url}/api/v5/flow/"

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as http:
                resp = await http.post(url, json=payload, headers=headers)
        except httpx.TimeoutException:
            # The provider may or may not have received it — do NOT assume failure
            # and do NOT blindly retry (could double-send). Caller marks UNKNOWN.
            logger.warning("[msg91] send timed out — outcome UNKNOWN")
            return SendResult(SendOutcome.UNKNOWN, detail="request timed out")
        except httpx.HTTPError as exc:  # connection errors etc. are retryable
            return SendResult(SendOutcome.RETRYABLE, detail=f"transport error: {exc}")

        return self._interpret_response(resp)

    def _interpret_response(self, resp: httpx.Response) -> SendResult:
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            secs = float(retry_after) if (retry_after or "").isdigit() else None
            return SendResult(SendOutcome.RETRYABLE, detail="rate limited", retry_after_seconds=secs)
        if resp.status_code >= 500:
            return SendResult(SendOutcome.RETRYABLE, detail=f"provider {resp.status_code}")

        try:
            data = resp.json()
        except ValueError:
            data = {}

        rtype = str(data.get("type", "")).lower()
        message = str(data.get("message", "")).strip()

        if resp.status_code < 300 and rtype == "success":
            # ``message`` is the requestId we later match DLR callbacks against.
            return SendResult(SendOutcome.ACCEPTED, provider_message_id=message or None)

        # 4xx or {"type":"error"} => a rejection. Auth / template / DLT problems
        # are permanent (retrying won't help). Keep the message (it is MSG91's,
        # not a secret) for the audit trail.
        if resp.status_code in (400, 401, 403):
            return SendResult(SendOutcome.PERMANENT, detail=message or f"HTTP {resp.status_code}")
        return SendResult(SendOutcome.RETRYABLE, detail=message or f"HTTP {resp.status_code}")

    def parse_delivery_callback(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Parse a MSG91 SMS DLR webhook.

        MSG91 can post either a single report object or a list of them, and keys
        vary slightly by account config — we read defensively.
        """
        items: list[dict]
        if isinstance(payload, list):
            items = [p for p in payload if isinstance(p, dict)]
        elif isinstance(payload.get("data"), list):
            items = [p for p in payload["data"] if isinstance(p, dict)]
        else:
            items = [payload]

        updates: list[DeliveryUpdate] = []
        for item in items:
            mid = (
                item.get("requestId")
                or item.get("request_id")
                or item.get("CRQID")
                or item.get("id")
            )
            if not mid:
                continue
            raw = str(item.get("status", "")).strip().lower()
            status = _STATUS_MAP.get(raw, NotificationStatus.UNKNOWN)
            detail = item.get("failureReason") or item.get("description") or raw or None
            tel = item.get("telNum") or item.get("number")
            updates.append(
                DeliveryUpdate(
                    provider_message_id=str(mid),
                    status=status,
                    detail=str(detail) if detail else None,
                    recipient_hint=(f"***{str(tel)[-2:]}" if tel else None),
                )
            )
        return updates

    def verify_callback(self, *, secret: str | None) -> bool:
        if not self._callback_secret:
            # Unconfigured => reject (never silently accept forged callbacks).
            logger.warning("[msg91] callback rejected: SMS_CALLBACK_SECRET not configured")
            return False
        return bool(secret) and hmac.compare_digest(secret, self._callback_secret)
