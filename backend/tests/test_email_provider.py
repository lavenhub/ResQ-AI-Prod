"""Unit tests: the free Email channel (SMTP mocked) + email-channel job building."""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.models import (
    EmergencyContact,
    Incident,
    NotificationStatus,
    TriggerSource,
)
from app.services.sms import OutboundSms, SendOutcome


def _run(coro):
    return asyncio.run(coro)


class _FakeSMTP:
    """Context-manager stand-in for smtplib.SMTP that records the sent message."""

    sent: list = []
    raise_exc: Exception | None = None

    def __init__(self, host, port, timeout=None):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, pw):
        pass

    def send_message(self, email):
        if _FakeSMTP.raise_exc:
            raise _FakeSMTP.raise_exc
        _FakeSMTP.sent.append(email)


@pytest.fixture
def email_provider(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "email")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USERNAME", "me@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("SMTP_FROM", "me@example.com")
    monkeypatch.setenv("SMS_CALLBACK_SECRET", "cbsecret")
    monkeypatch.setenv("SMTP_USE_TLS", "true")
    get_settings.cache_clear()

    import app.services.sms.email_provider as ep

    _FakeSMTP.sent = []
    _FakeSMTP.raise_exc = None
    monkeypatch.setattr(ep.smtplib, "SMTP", _FakeSMTP)

    provider = ep.EmailSmsProvider()
    yield provider
    get_settings.cache_clear()


def test_email_send_accepted_and_message_id(email_provider):
    r = _run(email_provider.send_emergency_sms(
        OutboundSms(to_e164="mum@example.com", subject="SOS", body="help",
                    variables={"_html": "<b>help</b>"})
    ))
    assert r.outcome == SendOutcome.ACCEPTED
    assert r.provider_message_id  # our Message-ID, for DLR correlation
    assert len(_FakeSMTP.sent) == 1
    msg = _FakeSMTP.sent[0]
    assert msg["To"] == "mum@example.com"
    assert msg["Subject"] == "SOS"
    assert f"<{r.provider_message_id}>" == msg["Message-ID"]


def test_email_missing_recipient_is_permanent(email_provider):
    r = _run(email_provider.send_emergency_sms(OutboundSms(to_e164="", subject="S", body="b")))
    assert r.outcome == SendOutcome.PERMANENT


def test_email_smtp_transient_error_is_retryable(email_provider):
    import smtplib

    _FakeSMTP.raise_exc = smtplib.SMTPException("temporary")
    r = _run(email_provider.send_emergency_sms(
        OutboundSms(to_e164="x@example.com", subject="S", body="b")
    ))
    assert r.outcome == SendOutcome.RETRYABLE


def test_email_auth_error_is_permanent(email_provider):
    import smtplib

    _FakeSMTP.raise_exc = smtplib.SMTPAuthenticationError(535, b"bad creds")
    r = _run(email_provider.send_emergency_sms(
        OutboundSms(to_e164="x@example.com", subject="S", body="b")
    ))
    assert r.outcome == SendOutcome.PERMANENT


def test_email_channel_and_validate(email_provider):
    assert email_provider.channel == "email"
    assert email_provider.validate_configuration() == []


def test_email_brevo_webhook_parse(email_provider):
    updates = email_provider.parse_delivery_callback(
        {"event": "delivered", "message-id": "<abc@resqai>"}
    )
    assert updates[0].status == NotificationStatus.DELIVERED
    assert updates[0].provider_message_id == "abc@resqai"  # angle brackets stripped

    bounced = email_provider.parse_delivery_callback({"event": "hard_bounce", "message-id": "x@y"})
    assert bounced[0].status == NotificationStatus.FAILED


def test_email_callback_secret(email_provider):
    assert email_provider.verify_callback(secret="cbsecret") is True
    assert email_provider.verify_callback(secret="nope") is False
    assert email_provider.verify_callback(secret=None) is False


def test_email_validate_reports_missing(monkeypatch):
    monkeypatch.setenv("SMS_PROVIDER", "email")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("SMTP_USERNAME", raising=False)
    monkeypatch.delenv("SMTP_PASSWORD", raising=False)
    get_settings.cache_clear()
    from app.services.sms.email_provider import EmailSmsProvider

    problems = EmailSmsProvider().validate_configuration()
    assert any("SMTP_HOST" in p for p in problems)
    get_settings.cache_clear()


# ── email-channel job building (notifier uses the active provider's channel) ──
def test_build_jobs_uses_email_channel_and_flags_missing_email(monkeypatch):
    from app.db import session_scope
    from app.services import notifier

    class _P:
        channel = "email"

    monkeypatch.setattr(notifier, "get_provider", lambda: _P())

    with session_scope() as s:
        inc = Incident(user_ref="+919000000111", trigger_source=TriggerSource.APP,
                       incident_type="Fall", latitude=19.0, longitude=72.0)
        s.add(inc)
        s.flush()
        with_email = EmergencyContact(user_ref="+919000000111", name="Mum",
                                      phone_e164="+919000000112", email="mum@example.com")
        no_email = EmergencyContact(user_ref="+919000000111", name="Dad",
                                    phone_e164="+919000000113", email=None)
        jobs = notifier.build_jobs_for_incident(s, inc, [with_email, no_email], victim_name="Asha")
        s.commit()
        by_phone = {j.recipient_phone: j for j in jobs}

    good = by_phone["+919000000112"]
    bad = by_phone["+919000000113"]
    assert good.channel == "email" and good.recipient_email == "mum@example.com"
    assert good.subject and good.status == NotificationStatus.PENDING
    # Contact with no email becomes a VISIBLE failed job, not a silent skip.
    assert bad.status == NotificationStatus.FAILED
    assert "no email" in (bad.last_error or "").lower()
