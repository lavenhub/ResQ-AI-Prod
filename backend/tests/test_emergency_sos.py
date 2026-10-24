"""Integration tests: the full SOS pipeline on the mock provider.

Covers mission Phase 13 integration cases: authed SOS -> persistent incident +
durable jobs; unauth blocked; no-contacts handled; idempotency (no duplicate
wave); independent per-recipient processing; provider-accepted vs delivered
distinct; duplicate/forged callbacks safe; missing location still alerts; manual
retry; cross-user isolation; cancellation.
"""

from __future__ import annotations

from app.services.notifier import drain_pending

CB = "/api/sms/callback?secret=test-callback-secret"


def _add_contact(client, headers, name, phone, active=True):
    r = client.post(
        "/api/contacts", headers=headers, json={"name": name, "phone": phone, "active": active}
    )
    assert r.status_code == 201, r.text
    return r.json()


def test_sos_requires_auth(client):
    assert client.post("/api/emergency/sos", json={}).status_code == 401


def test_sos_without_contacts_is_409(client, unique_user):
    _, headers = unique_user
    r = client.post("/api/emergency/sos", headers=headers, json={"incident_type": "Fall"})
    assert r.status_code == 409
    assert "contact" in r.json()["detail"].lower()


def test_sos_creates_incident_and_pending_jobs(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919811111111")
    _add_contact(client, headers, "Dad", "+919822222222")

    r = client.post(
        "/api/emergency/sos",
        headers=headers,
        json={"incident_type": "Road Accident", "latitude": 19.07, "longitude": 72.87,
              "victim_name": "Asha"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True
    assert body["status"] == "ACTIVE"
    assert body["location_available"] is True
    assert len(body["notifications"]) == 2
    # Before any worker run, everything is PENDING — never claimed as sent.
    assert all(n["status"] == "pending" for n in body["notifications"])
    assert body["notification_summary"]["overall"] == "processing"


def test_missing_location_still_queues(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919833333333")
    r = client.post("/api/emergency/sos", headers=headers, json={"incident_type": "Medical"})
    assert r.status_code == 200
    assert r.json()["location_available"] is False
    assert len(r.json()["notifications"]) == 1


def test_idempotent_sos_does_not_double_queue(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919844444444")
    payload = {"incident_type": "Fire", "idempotency_key": "abc-123"}
    first = client.post("/api/emergency/sos", headers=headers, json=payload).json()
    second = client.post("/api/emergency/sos", headers=headers, json=payload).json()
    assert first["incident_id"] == second["incident_id"]
    assert second["created"] is False
    # No second wave of messages.
    assert len(second["notifications"]) == len(first["notifications"]) == 1


def test_worker_marks_accepted_not_delivered_then_callback_delivers(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919855555551")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]

    drain_pending()  # process the outbox once (mock accepts)

    status = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()
    n = status["notifications"][0]
    assert n["status"] == "provider_accepted"  # accepted != delivered
    assert n["provider_message_id"]
    assert status["notification_summary"]["overall"] == "provider_accepted"

    # Now a carrier DLR confirms delivery.
    mid = n["provider_message_id"]
    cb = client.post(CB, json={"message_id": mid, "status": "delivered"})
    assert cb.status_code == 200 and cb.json()["applied"] == 1

    after = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()
    assert after["notifications"][0]["status"] == "delivered"
    assert after["notifications"][0]["delivered_at"]
    assert after["notification_summary"]["overall"] == "all_delivered"


def test_duplicate_and_terminal_callbacks_do_not_regress(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919855555552")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    drain_pending()
    mid = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()[
        "notifications"][0]["provider_message_id"]

    # Deliver, then a late "failed" arrives out of order + a duplicate "delivered".
    client.post(CB, json={"message_id": mid, "status": "delivered"})
    client.post(CB, json={"message_id": mid, "status": "failed"})      # must NOT regress
    client.post(CB, json={"message_id": mid, "status": "delivered"})   # duplicate harmless

    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "delivered"


def test_forged_callback_rejected(client):
    r = client.post("/api/sms/callback?secret=WRONG", json={"message_id": "x", "status": "delivered"})
    assert r.status_code == 401


def test_independent_recipients_one_permanent_fail(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Good", "+919866666661")
    _add_contact(client, headers, "Bad", "+919866660000")  # ...0000 => permanent fail (mock)
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    notifs = {n["recipient_phone"]: n for n in
              client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"]}
    assert notifs["+919866666661"]["status"] == "provider_accepted"
    assert notifs["+919866660000"]["status"] == "failed"  # one bad number didn't block the other


def test_retryable_eventually_fails_after_max_attempts(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Flaky", "+919877779999")  # ...9999 => retryable (mock)
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    # Drain repeatedly; backoff schedules next_attempt into the future, so sleep a bit.
    import time

    for _ in range(6):
        drain_pending()
        time.sleep(0.05)
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "failed"
    assert n["attempts"] >= 1


def test_timeout_outcome_is_unknown_not_retried(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Timeout", "+919877777777")  # ...7777 => UNKNOWN (mock)
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "unknown"  # not blindly retried/marked sent


def test_manual_retry_of_failed(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Bad", "+919888880000")  # permanent fail
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    nid = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()[
        "notifications"][0]["id"]
    r = client.post(f"/api/emergency/notifications/{nid}/retry", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "pending"


def test_cannot_read_another_users_incident(client, unique_user, auth_other):
    _, headers = unique_user
    _, other = auth_other
    _add_contact(client, headers, "Mum", "+919899990001")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    assert client.get(f"/api/emergency/incidents/{inc_id}", headers=other).status_code == 404


def test_cancel_incident_stops_pending(client, unique_user):
    _, headers = unique_user
    _add_contact(client, headers, "Mum", "+919899990002")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    r = client.post(f"/api/emergency/incidents/{inc_id}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    # Pending jobs are stopped (marked failed) before any send.
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "failed"


def test_sms_health_reports_mock(client):
    h = client.get("/api/sms/health").json()
    assert h["provider"] == "mock"
    assert h["isMock"] is True
    assert h["realSendingConfigured"] is False
