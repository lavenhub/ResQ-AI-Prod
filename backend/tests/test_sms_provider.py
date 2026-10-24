"""Unit tests: SMS provider abstraction, mock + MSG91 (httpx mocked)."""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.models import NotificationStatus
from app.services.sms import OutboundSms, SendOutcome
from app.services.sms.mock import MockSmsProvider


def _run(coro):
    return asyncio.run(coro)


# ── Mock provider ─────────────────────────────────────────────────────────────
def test_mock_accepts_and_records_but_does_not_claim_delivery():
    p = MockSmsProvider()
    r = _run(p.send_emergency_sms(OutboundSms(to_e164="+919876543210", body="hi")))
    assert r.outcome == SendOutcome.ACCEPTED
    assert r.provider_message_id
    assert len(p.sent) == 1
    # ACCEPTED is explicitly not DELIVERED.
    assert r.outcome != NotificationStatus.DELIVERED


@pytest.mark.parametrize(
    "tail,expected",
    [
        ("0000", SendOutcome.PERMANENT),
        ("9999", SendOutcome.RETRYABLE),
        ("7777", SendOutcome.UNKNOWN),
    ],
)
def test_mock_failure_injection(tail, expected):
    p = MockSmsProvider()
    r = _run(p.send_emergency_sms(OutboundSms(to_e164=f"+91987654{tail}", body="x")))
    assert r.outcome == expected


def test_mock_callback_parse_and_verify():
    p = MockSmsProvider(callback_secret="s3cret")
    assert p.verify_callback(secret="s3cret") is True
    assert p.verify_callback(secret="wrong") is False
    updates = p.parse_delivery_callback({"message_id": "m1", "status": "delivered"})
    assert len(updates) == 1
    assert updates[0].status == NotificationStatus.DELIVERED


# ── MSG91 provider with httpx mocked ──────────────────────────────────────────
class _FakeResp:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _FakeClient:
    """Async-context-manager stand-in for httpx.AsyncClient."""

    captured: dict = {}

    def __init__(self, result, *, timeout=None):
        self._result = result

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None, headers=None):
        _FakeClient.captured = {"url": url, "json": json, "headers": headers}
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


@pytest.fixture
def msg91(monkeypatch):
    """Factory that builds a configured Msg91SmsProvider whose httpx returns a
    canned result."""
    monkeypatch.setenv("SMS_PROVIDER", "msg91")
    monkeypatch.setenv("MSG91_AUTH_KEY", "AUTHKEY123")
    monkeypatch.setenv("MSG91_TEMPLATE_ID", "FLOW123")
    monkeypatch.setenv("MSG91_SENDER_ID", "RESQAI")
    monkeypatch.setenv("SMS_CALLBACK_SECRET", "cbsecret")
    get_settings.cache_clear()

    import httpx

    from app.services.sms.msg91 import Msg91SmsProvider

    def _make(result):
        provider = Msg91SmsProvider()

        def _factory(*a, **kw):
            return _FakeClient(result, **kw)

        monkeypatch.setattr(httpx, "AsyncClient", _factory)
        return provider

    yield _make
    get_settings.cache_clear()


def test_msg91_success_maps_requestid(msg91):
    provider = msg91(_FakeResp(200, {"type": "success", "message": "req-abc-123"}))
    r = _run(provider.send_emergency_sms(
        OutboundSms(to_e164="+919876543210", body="hi", variables={"name": "A"})
    ))
    assert r.outcome == SendOutcome.ACCEPTED
    assert r.provider_message_id == "req-abc-123"
    # Verified wire shape: flow_id + sender + recipients[0].mobiles (no '+').
    sent = _FakeClient.captured["json"]
    assert sent["flow_id"] == "FLOW123"
    assert sent["sender"] == "RESQAI"
    assert sent["recipients"][0]["mobiles"] == "919876543210"
    assert sent["recipients"][0]["name"] == "A"
    assert _FakeClient.captured["headers"]["authkey"] == "AUTHKEY123"


def test_msg91_error_type_is_permanent(msg91):
    provider = msg91(_FakeResp(400, {"type": "error", "message": "bad template"}))
    r = _run(provider.send_emergency_sms(OutboundSms(to_e164="+919876543210", body="x")))
    assert r.outcome == SendOutcome.PERMANENT
    assert "bad template" in (r.detail or "")


def test_msg91_rate_limit_is_retryable(msg91):
    provider = msg91(_FakeResp(429, {"type": "error"}, headers={"Retry-After": "30"}))
    r = _run(provider.send_emergency_sms(OutboundSms(to_e164="+919876543210", body="x")))
    assert r.outcome == SendOutcome.RETRYABLE
    assert r.retry_after_seconds == 30


def test_msg91_timeout_is_unknown_not_failure(msg91):
    import httpx

    provider = msg91(httpx.TimeoutException("timed out"))
    r = _run(provider.send_emergency_sms(OutboundSms(to_e164="+919876543210", body="x")))
    # Must be UNKNOWN so the worker does NOT blindly resend (possible double send).
    assert r.outcome == SendOutcome.UNKNOWN


def test_msg91_5xx_is_retryable(msg91):
    provider = msg91(_FakeResp(503, {"type": "error"}))
    r = _run(provider.send_emergency_sms(OutboundSms(to_e164="+919876543210", body="x")))
    assert r.outcome == SendOutcome.RETRYABLE


def test_msg91_callback_parse_maps_statuses(msg91):
    provider = msg91(_FakeResp(200, {"type": "success", "message": "x"}))
    delivered = provider.parse_delivery_callback({"requestId": "r1", "status": "delivered"})
    failed = provider.parse_delivery_callback({"requestId": "r2", "status": "failed"})
    assert delivered[0].status == NotificationStatus.DELIVERED
    assert delivered[0].provider_message_id == "r1"
    assert failed[0].status == NotificationStatus.FAILED


def test_msg91_rejects_callback_without_secret(msg91):
    provider = msg91(_FakeResp(200, {"type": "success", "message": "x"}))
    assert provider.verify_callback(secret="cbsecret") is True
    assert provider.verify_callback(secret="nope") is False
    assert provider.verify_callback(secret=None) is False


def test_msg91_validate_configuration_reports_missing(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "msg91")
    monkeypatch.delenv("MSG91_AUTH_KEY", raising=False)
    monkeypatch.delenv("MSG91_TEMPLATE_ID", raising=False)
    monkeypatch.delenv("MSG91_SENDER_ID", raising=False)
    monkeypatch.delenv("SMS_CALLBACK_SECRET", raising=False)
    get_settings.cache_clear()
    from app.services.sms.msg91 import Msg91SmsProvider

    problems = Msg91SmsProvider().validate_configuration()
    assert any("MSG91_AUTH_KEY" in p for p in problems)
    get_settings.cache_clear()
