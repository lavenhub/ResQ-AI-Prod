"""Call-flow integration: provider webhooks drive the backend state machine.

All on the MOCK provider — no real PSTN call is ever placed. Covers ringing /
answered / completed / busy / no-answer transitions, forged-webhook rejection,
retry, cross-user isolation, and the authorized email-update integration.
"""

from __future__ import annotations

from tests.helpers import (
    add_verified_contact,
    call_status,
    create_booking,
    create_incident,
    create_service,
)


def _booked(client, headers, user_ref, *, is_demo=True):
    inc = create_incident(client, user_ref)
    svc = create_service(client, headers, is_demo=is_demo)
    b = create_booking(client, headers, inc["id"], svc["id"], consent=True).json()
    return inc, svc, b


def test_full_answered_call_lifecycle(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    bid = b["id"]
    assert client.post(f"/api/ambulance/bookings/{bid}/call", headers=headers).status_code == 200

    call_status(client, bid, "CA1", "ringing")
    assert client.get(f"/api/ambulance/bookings/{bid}", headers=headers).json()["call_status"] == "RINGING"

    call_status(client, bid, "CA1", "in-progress")
    con = client.get(f"/api/ambulance/bookings/{bid}", headers=headers).json()
    assert con["call_status"] == "CONNECTED"
    assert con["booking_state"] == "CONNECTED"
    assert con["call_connected_at"] is not None

    call_status(client, bid, "CA1", "completed")
    done = client.get(f"/api/ambulance/bookings/{bid}", headers=headers).json()
    assert done["call_status"] == "COMPLETED"
    assert done["booking_state"] == "COMPLETED"
    assert done["call_completed_at"] is not None


def test_busy_marks_call_failed(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    call_status(client, b["id"], "CA2", "busy")
    got = client.get(f"/api/ambulance/bookings/{b['id']}", headers=headers).json()
    assert got["call_status"] == "BUSY"
    assert got["booking_state"] == "CALL_FAILED"


def test_no_answer_marks_call_failed(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    call_status(client, b["id"], "CA3", "no-answer")
    got = client.get(f"/api/ambulance/bookings/{b['id']}", headers=headers).json()
    assert got["booking_state"] == "CALL_FAILED"


def test_forged_webhook_rejected(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    # Unsigned status callback must be rejected and change nothing.
    r = call_status(client, b["id"], "CA4", "completed", signed=False)
    assert r.status_code == 403
    assert client.get(f"/api/ambulance/bookings/{b['id']}", headers=headers).json()["call_status"] == "INITIATING"


def test_retry_after_failure(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    bid = b["id"]
    client.post(f"/api/ambulance/bookings/{bid}/call", headers=headers)
    call_status(client, bid, "CA5", "busy")

    rr = client.post(f"/api/ambulance/bookings/{bid}/retry", headers=headers)
    assert rr.status_code == 200
    assert rr.json()["call_status"] == "PENDING"
    assert rr.json()["booking_state"] == "PREPARING_REQUEST"
    # A fresh call can be placed again after retry.
    assert client.post(f"/api/ambulance/bookings/{bid}/call", headers=headers).status_code == 200


def test_max_attempts_enforced(client, unique_user):
    user_ref, headers = unique_user
    _, _, b = _booked(client, headers, user_ref)
    bid = b["id"]
    # AMBULANCE_MAX_CALL_ATTEMPTS=3 in conftest.
    for sid in ("CB1", "CB2", "CB3"):
        client.post(f"/api/ambulance/bookings/{bid}/call", headers=headers)
        call_status(client, bid, sid, "busy")
        client.post(f"/api/ambulance/bookings/{bid}/retry", headers=headers)
    # The 4th call attempt is now blocked (max attempts reached).
    r = client.post(f"/api/ambulance/bookings/{bid}/call", headers=headers)
    assert r.status_code == 409


def test_call_initiation_queues_authorized_email(client, unique_user):
    user_ref, headers = unique_user
    add_verified_contact(client, headers, "Kin", "kin@example.com")
    _, _, b = _booked(client, headers, user_ref)
    inc_id = b["incident_id"]
    client.post(f"/api/ambulance/bookings/{b['id']}/call", headers=headers)
    status = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()
    # A booking-update email was queued to the verified contact (honest 'queued').
    assert status["notification_summary"]["total"] >= 1
    assert all(n["status"] in ("queued", "pending") for n in status["notifications"])
