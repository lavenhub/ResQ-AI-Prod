"""Shared test helpers (email-based contacts + verification + webhooks)."""

from __future__ import annotations

# Webhook header the mock provider requires (matches EMAIL_WEBHOOK_SECRET in conftest).
WEBHOOK_HEADERS = {"x-webhook-secret": "test-webhook-secret"}
WEBHOOK_URL = "/api/email/webhook"


def add_contact(client, headers, name, email, active=True):
    """Create an emergency email contact and return the created record."""
    r = client.post(
        "/api/contacts", headers=headers, json={"name": name, "email": email, "active": active}
    )
    assert r.status_code == 201, r.text
    return r.json()


def verify_contact(client, headers, contact_id):
    """Run the real verification loop via the dev link (mock provider only)."""
    r = client.post(f"/api/contacts/{contact_id}/send-verification", headers=headers)
    assert r.status_code == 200, r.text
    url = r.json()["dev_verification_url"]
    assert url, "mock provider should expose a dev verification url"
    path = url.replace("http://testserver", "")
    assert client.get(path).status_code == 200


def add_verified_contact(client, headers, name, email, active=True):
    c = add_contact(client, headers, name, email, active=active)
    verify_contact(client, headers, c["id"])
    return c


# ── Ambulance coordination helpers ──────────────────────────────────────────────
# Header the MOCK telephony provider requires (matches TELEPHONY_WEBHOOK_SECRET
# in conftest), so tests can also assert that unsigned webhooks are rejected.
TELEPHONY_HEADERS = {"x-telephony-signature": "test-telephony-secret"}


def create_incident(client, user_ref, *, incident_type="Road Accident", lat=19.07, lng=72.87,
                    address="MG Road, Mumbai"):
    """Create an incident owned by ``user_ref`` (the unauthenticated incidents
    endpoint accepts an explicit user_ref)."""
    r = client.post("/api/incidents", json={
        "user_ref": user_ref, "incident_type": incident_type,
        "latitude": lat, "longitude": lng, "address": address,
        "reported_symptoms": "chest pain",
    })
    assert r.status_code in (200, 201), r.text
    return r.json()


def create_service(client, headers, *, name="City Hospital", phone="+919812345678",
                   is_demo=False, verify=False):
    r = client.post("/api/ambulance/services", headers=headers, json={
        "name": name, "phone": phone, "city": "Mumbai", "is_demo": is_demo,
    })
    assert r.status_code == 201, r.text
    svc = r.json()
    if verify:
        rv = client.post(f"/api/ambulance/services/{svc['id']}/verify", headers=headers)
        assert rv.status_code == 200, rv.text
        svc = rv.json()
    return svc


def create_booking(client, headers, incident_id, service_id, *, consent=True, **extra):
    body = {"incident_id": incident_id, "service_id": service_id, "consent": consent}
    body.update(extra)
    r = client.post("/api/ambulance/bookings", headers=headers, json=body)
    return r


def call_status(client, booking_id, call_sid, status_value, *, signed=True):
    """POST a provider-style call-status callback (form-encoded, Twilio field names)."""
    headers = dict(TELEPHONY_HEADERS) if signed else {}
    return client.post(
        f"/api/ambulance/telephony/status/{booking_id}",
        data={"CallSid": call_sid, "CallStatus": status_value},
        headers=headers,
    )
