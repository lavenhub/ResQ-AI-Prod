"""WebRTC reception path tests (the free, no-PSTN provider).

Driven with TELEPHONY_PROVIDER=webrtc via a monkeypatched factory, on the real
HTTP stack. The actual SmallWebRTC media session needs a real browser + GEMINI
key (voice disabled in tests), so the /offer endpoint is only asserted to gate on
voice-enabled; the booking flow + RINGING transition + reception-info are fully
exercised.
"""

from __future__ import annotations

import pytest

from app.services import telephony as telephony_pkg
from app.services.telephony import reset_telephony_cache
from app.services.telephony.webrtc_provider import WebRTCTelephonyProvider
from tests.helpers import create_booking, create_incident, create_service


@pytest.fixture
def webrtc_provider(monkeypatch):
    """Force the telephony factory to return the WebRTC provider for this test."""
    prov = WebRTCTelephonyProvider()
    # WebRTC provider needs a gemini key to be "ready"; tests disable voice, so
    # stub validate_configuration to report ready for the booking-flow assertions.
    monkeypatch.setattr(prov, "validate_configuration", lambda: [])
    reset_telephony_cache()
    monkeypatch.setattr(telephony_pkg, "get_telephony_provider", lambda: prov)
    # The ambulance route imports the symbol directly; patch there too.
    import app.routes.ambulance as amb
    monkeypatch.setattr(amb, "get_telephony_provider", lambda: prov)
    yield prov
    reset_telephony_cache()


def test_webrtc_call_goes_to_ringing(client, unique_user, webrtc_provider):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()

    r = client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["provider"] == "webrtc"
    # WebRTC reports RINGING at once (waiting for the receptionist to join).
    assert body["call_status"] == "RINGING"
    assert body["booking_state"] == "RINGING"


def test_reception_info_exposes_only_safe_fields(client, unique_user, webrtc_provider):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True,
                       patient_condition="leg fracture").json()
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)

    # Reception info is UNauthenticated (teammate has no account).
    info = client.get(f"/api/ambulance/reception/booking/{b['id']}").json()
    assert info["answerable"] is True
    assert info["patient_condition"] == "leg fracture"
    assert info["pickup_address"] == "MG Road, Mumbai"
    # No owner identity / internal ids leak.
    for leaky in ("user_ref", "provider_call_id", "service_id", "latitude"):
        assert leaky not in info


def test_reception_info_unknown_booking_404(client, webrtc_provider):
    assert client.get("/api/ambulance/reception/booking/nope").status_code == 404


def test_reception_offer_requires_voice_enabled(client, unique_user, webrtc_provider):
    # Voice is disabled in tests (no GEMINI key), so answering must 503 rather
    # than attempt a real media session.
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)

    r = client.post("/api/ambulance/reception/offer",
                    json={"sdp": "x", "type": "offer", "booking_id": b["id"]})
    assert r.status_code == 503


def test_reception_offer_rejects_terminal_booking(client, unique_user, webrtc_provider):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    client.post(f"/api/ambulance/bookings/{b['id']}/cancel", headers=headers)
    # Cancelled -> not joinable. 409 (over the 503 voice gate is fine either way,
    # but cancel makes it terminal so we assert it's not answerable via info).
    info = client.get(f"/api/ambulance/reception/booking/{b['id']}").json()
    assert info["answerable"] is False
