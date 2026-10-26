"""Booking creation + call authorization + duplicate-call prevention tests."""

from __future__ import annotations

from tests.helpers import create_booking, create_incident, create_service


def test_create_booking_from_incident(client, unique_user):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    r = create_booking(client, headers, inc["id"], svc["id"], consent=True)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["booking_state"] == "PREPARING_REQUEST"
    assert b["call_status"] == "PENDING"
    assert b["availability"] == "UNKNOWN"
    # Location copied from the incident; never invented.
    assert b["pickup_address"] == "MG Road, Mumbai"
    assert b["service_is_demo"] is True
    # ETA / charges / reference are empty until actually provided.
    assert b["eta_text"] is None and b["charges_text"] is None and b["booking_reference"] is None


def test_create_booking_auto_creates_incident(client, unique_user):
    user_ref, headers = unique_user
    svc = create_service(client, headers, is_demo=True)
    # No incident_id — the backend creates one owned by the caller from the fields.
    r = client.post("/api/ambulance/bookings", headers=headers, json={
        "service_id": svc["id"], "consent": True, "emergency_type": "Cardiac",
        "patient_condition": "chest pain", "latitude": 19.1, "longitude": 72.9,
        "address": "Bandra, Mumbai",
    })
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["incident_id"]
    assert b["pickup_address"] == "Bandra, Mumbai"
    # The auto-created incident is owned by the caller and usable for the call.
    assert client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers).status_code == 200


def test_cannot_book_against_another_users_incident(client, unique_user, auth_other):
    user_ref, headers = unique_user
    other_ref, _ = auth_other
    inc = create_incident(client, other_ref)  # incident owned by the OTHER user
    svc = create_service(client, headers, is_demo=True)
    r = create_booking(client, headers, inc["id"], svc["id"])
    assert r.status_code == 422
    assert "authorized" in r.json()["detail"].lower()


def test_cannot_book_unverified_nondemo_service(client, unique_user):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=False, verify=False)
    r = create_booking(client, headers, inc["id"], svc["id"])
    assert r.status_code == 422
    assert "verif" in r.json()["detail"].lower()


def test_call_requires_consent(client, unique_user):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=False).json()
    r = client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    assert r.status_code == 409
    assert "consent" in r.json()["detail"].lower()


def test_place_call_and_duplicate_prevented(client, unique_user):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()

    r1 = client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    assert r1.status_code == 200, r1.text
    assert r1.json()["call_status"] == "INITIATING"
    assert r1.json()["booking_state"] == "INITIATING_CALL"

    # A second tap while a call is in flight must be refused (no double dial).
    r2 = client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    assert r2.status_code == 409
    assert "in progress" in r2.json()["detail"].lower()


def test_inactive_service_blocks_call(client, unique_user):
    user_ref, headers = unique_user
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()
    client.patch(f"/api/ambulance/services/{svc['id']}", headers=headers, json={"active": False})
    r = client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    assert r.status_code == 409


def test_booking_not_visible_to_other_user(client, unique_user, auth_other):
    user_ref, headers = unique_user
    _, other_headers = auth_other
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=True)
    b = create_booking(client, headers, inc["id"], svc["id"]).json()
    assert client.get(f"/api/ambulance/bookings/{b['id']}", headers=other_headers).status_code == 404
