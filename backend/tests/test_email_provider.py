"""Unit tests for the email providers (mock + Resend), with NO network.

Covers: mock send outcomes + webhook parsing; Resend response interpretation;
Resend Svix webhook signature verification (real crypto, both valid and forged);
Resend event parsing including bounce/complaint classification.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time

import httpx
import pytest

from app.models import NotificationStatus
from app.services.email.base import OutboundEmail, SendOutcome
from app.services.email.mock import MockEmailProvider
from app.services.email.resend_provider import ResendEmailProvider


def _run(coro):
    return asyncio.run(coro)


# ── Mock provider ──────────────────────────────────────────────────────────────
def test_mock_send_accepts_normal_and_records():
    p = MockEmailProvider()
    r = _run(p.send(OutboundEmail(to_email="a@example.com", subject="s", text="t")))
    assert r.outcome == SendOutcome.ACCEPTED
    assert r.provider_message_id
    assert p.sent and p.sent[0].to == "a@example.com"


@pytest.mark.parametrize(
    "local,expected",
    [
        ("permanent", SendOutcome.PERMANENT),
        ("retryable", SendOutcome.RETRYABLE),
        ("timeout", SendOutcome.UNKNOWN),
    ],
)
def test_mock_send_failure_injection(local, expected):
    p = MockEmailProvider()
    r = _run(p.send(OutboundEmail(to_email=f"{local}@example.com", subject="s", text="t")))
    assert r.outcome == expected


def test_mock_webhook_parse_and_verify():
    p = MockEmailProvider(webhook_secret="sek")
    updates = p.parse_webhook_event({"type": "email.delivered", "data": {"email_id": "m1"}})
    assert len(updates) == 1
    assert updates[0].status == NotificationStatus.DELIVERED
    assert p.verify_webhook(raw_body=b"{}", headers={"x-webhook-secret": "sek"}) is True
    assert p.verify_webhook(raw_body=b"{}", headers={"x-webhook-secret": "no"}) is False
    # No secret configured => accept (dev convenience).
    assert MockEmailProvider().verify_webhook(raw_body=b"{}", headers={}) is True


# ── Resend: response interpretation ─────────────────────────────────────────────
def _resend() -> ResendEmailProvider:
    p = ResendEmailProvider()
    p._api_key = "re_test"
    p._from_address = "alerts@resqai.test"
    return p


def test_resend_interpret_responses():
    p = _resend()
    assert p._interpret_response(httpx.Response(200, json={"id": "abc"})).outcome == SendOutcome.ACCEPTED
    assert p._interpret_response(httpx.Response(200, json={"id": "abc"})).provider_message_id == "abc"
    assert p._interpret_response(httpx.Response(429, headers={"Retry-After": "7"})).outcome == SendOutcome.RETRYABLE
    assert p._interpret_response(httpx.Response(429, headers={"Retry-After": "7"})).retry_after_seconds == 7
    assert p._interpret_response(httpx.Response(503)).outcome == SendOutcome.RETRYABLE
    assert p._interpret_response(httpx.Response(401, json={"message": "bad key"})).outcome == SendOutcome.PERMANENT
    assert p._interpret_response(httpx.Response(422, json={"message": "invalid to"})).outcome == SendOutcome.PERMANENT


def test_resend_send_timeout_is_unknown(monkeypatch):
    p = _resend()

    async def _boom(*a, **k):
        raise httpx.TimeoutException("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "post", _boom)
    r = _run(p.send(OutboundEmail(to_email="x@example.com", subject="s", text="t")))
    assert r.outcome == SendOutcome.UNKNOWN


def test_resend_send_success(monkeypatch):
    p = _resend()
    captured = {}

    async def _ok(self, url, json=None, headers=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return httpx.Response(200, json={"id": "email_123"})

    monkeypatch.setattr(httpx.AsyncClient, "post", _ok)
    r = _run(
        p.send(
            OutboundEmail(
                to_email="x@example.com", subject="Hi", text="t", html="<b>t</b>",
                idempotency_key="job-1",
            )
        )
    )
    assert r.outcome == SendOutcome.ACCEPTED
    assert r.provider_message_id == "email_123"
    assert captured["url"] == "https://api.resend.com/emails"
    assert captured["json"]["to"] == ["x@example.com"]
    assert captured["json"]["html"] == "<b>t</b>"
    assert captured["headers"]["Authorization"] == "Bearer re_test"
    assert captured["headers"]["Idempotency-Key"] == "job-1"


def test_resend_not_configured_is_permanent():
    p = ResendEmailProvider()
    p._api_key = ""  # missing key
    p._from_address = ""
    r = _run(p.send(OutboundEmail(to_email="x@example.com", subject="s", text="t")))
    assert r.outcome == SendOutcome.PERMANENT


# ── Resend: Svix webhook signature verification (real crypto) ───────────────────
def _sign(secret_b64_key: bytes, svix_id: str, ts: str, body: bytes) -> str:
    signed = b"%s.%s.%s" % (svix_id.encode(), ts.encode(), body)
    sig = base64.b64encode(hmac.new(secret_b64_key, signed, hashlib.sha256).digest()).decode()
    return f"v1,{sig}"


def test_resend_verify_webhook_valid_and_forged():
    key = b"0123456789abcdef0123456789abcdef"
    secret = "whsec_" + base64.b64encode(key).decode()
    p = ResendEmailProvider()
    p._webhook_secret = secret
    p._tolerance = 300

    body = b'{"type":"email.delivered","data":{"email_id":"e1"}}'
    svix_id = "msg_1"
    ts = str(int(time.time()))
    good_sig = _sign(key, svix_id, ts, body)
    headers = {"svix-id": svix_id, "svix-timestamp": ts, "svix-signature": good_sig}

    assert p.verify_webhook(raw_body=body, headers=headers) is True
    # Tampered body fails.
    assert p.verify_webhook(raw_body=body + b" ", headers=headers) is False
    # Wrong signature fails.
    bad = dict(headers, **{"svix-signature": "v1,AAAA"})
    assert p.verify_webhook(raw_body=body, headers=bad) is False
    # Stale timestamp fails (replay guard).
    old_ts = str(int(time.time()) - 10_000)
    stale = {"svix-id": svix_id, "svix-timestamp": old_ts,
             "svix-signature": _sign(key, svix_id, old_ts, body)}
    assert p.verify_webhook(raw_body=body, headers=stale) is False
    # Missing secret => reject.
    p2 = ResendEmailProvider()
    p2._webhook_secret = ""
    assert p2.verify_webhook(raw_body=body, headers=headers) is False


def test_resend_verify_accepts_multiple_signatures():
    key = b"0123456789abcdef0123456789abcdef"
    p = ResendEmailProvider()
    p._webhook_secret = "whsec_" + base64.b64encode(key).decode()
    p._tolerance = 300
    body = b"{}"
    svix_id, ts = "m", str(int(time.time()))
    good = _sign(key, svix_id, ts, body)
    multi = f"v1,other {good} v2,another"
    assert p.verify_webhook(
        raw_body=body, headers={"svix-id": svix_id, "svix-timestamp": ts, "svix-signature": multi}
    ) is True


# ── Resend: event parsing ───────────────────────────────────────────────────────
def test_resend_parse_events():
    p = _resend()
    delivered = p.parse_webhook_event({"type": "email.delivered", "data": {"email_id": "e1"}})
    assert delivered[0].status == NotificationStatus.DELIVERED
    assert delivered[0].is_bounce is False

    bounced = p.parse_webhook_event(
        {"type": "email.bounced",
         "data": {"email_id": "e2", "bounce": {"type": "Permanent", "message": "mailbox full"}}}
    )
    assert bounced[0].status == NotificationStatus.BOUNCED
    assert bounced[0].is_bounce is True
    assert "mailbox full" in (bounced[0].detail or "")

    complained = p.parse_webhook_event({"type": "email.complained", "data": {"email_id": "e3"}})
    assert complained[0].status is None  # complaint doesn't change delivery status
    assert complained[0].is_complaint is True

    deferred = p.parse_webhook_event({"type": "email.delivery_delayed", "data": {"email_id": "e4"}})
    assert deferred[0].status == NotificationStatus.DEFERRED

    # Ignored events (opened/clicked) and missing id produce no updates.
    assert p.parse_webhook_event({"type": "email.opened", "data": {"email_id": "e5"}}) == []
    assert p.parse_webhook_event({"type": "email.delivered", "data": {}}) == []
