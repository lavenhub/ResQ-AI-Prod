"""Telephony provider abstraction tests (mock + Twilio), no real calls placed."""

from __future__ import annotations

import base64
import hashlib
import hmac

from app.models import CallStatus
from app.services.telephony import get_telephony_provider
from app.services.telephony.twilio_provider import TwilioTelephonyProvider, map_twilio_status


def test_default_provider_is_mock():
    provider = get_telephony_provider()
    assert provider.is_mock is True
    assert provider.name == "mock"
    assert provider.validate_configuration() == []


def test_twilio_status_mapping():
    assert map_twilio_status("ringing") == CallStatus.RINGING
    assert map_twilio_status("in-progress") == CallStatus.CONNECTED
    assert map_twilio_status("completed") == CallStatus.COMPLETED
    assert map_twilio_status("busy") == CallStatus.BUSY
    assert map_twilio_status("no-answer") == CallStatus.NO_ANSWER
    assert map_twilio_status("failed") == CallStatus.FAILED
    assert map_twilio_status("garbage") is None
    assert map_twilio_status(None) is None


def test_twilio_validate_configuration_reports_missing_creds():
    # In mock-mode conftest the Twilio creds are unset -> problems reported.
    problems = TwilioTelephonyProvider().validate_configuration()
    joined = " ".join(problems).lower()
    assert "account_sid" in joined or "auth_token" in joined


def test_twilio_webhook_signature_roundtrip():
    """Verify the documented Twilio X-Twilio-Signature algorithm accepts a
    correctly-signed request and rejects a forged one."""
    p = TwilioTelephonyProvider()
    p._auth_token = "test-token"  # noqa: SLF001 — exercising the algorithm directly
    url = "https://example.com/api/ambulance/telephony/status/bk1"
    form = {"CallSid": "CA123", "CallStatus": "completed"}

    s = url + "".join(k + form[k] for k in sorted(form))
    sig = base64.b64encode(
        hmac.new(b"test-token", s.encode(), hashlib.sha1).digest()
    ).decode()

    assert p.verify_webhook(url=url, raw_body=b"", form=form, headers={"X-Twilio-Signature": sig})
    assert not p.verify_webhook(url=url, raw_body=b"", form=form, headers={"X-Twilio-Signature": "wrong"})
    assert not p.verify_webhook(url=url, raw_body=b"", form=form, headers={})


def test_twilio_answer_document_is_bidirectional_stream():
    doc = TwilioTelephonyProvider().build_call_answer_document(
        media_ws_url="wss://example.com/api/ambulance/telephony/media/bk1", booking_id="bk1"
    )
    assert "<Connect>" in doc.body and "<Stream" in doc.body
    assert "wss://example.com" in doc.body
    assert doc.media_type == "application/xml"
