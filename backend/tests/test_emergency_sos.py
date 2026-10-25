"""Integration tests: the full SOS email pipeline on the mock provider.

Covers mission Phase 14 integration cases: authed SOS -> persistent incident +
durable jobs; unauth blocked; no-verified-contacts handled; idempotency (no
duplicate wave); independent per-recipient processing; provider-accepted vs
delivered distinct; duplicate/forged webhooks safe; bounce suppression; missing
location still alerts; manual retry; cross-user isolation; cancellation.
"""

from __future__ import annotations

from tests.helpers import WEBHOOK_HEADERS, WEBHOOK_URL, add_contact, add_verified_contact

from app.services.notifier import drain_pending


def _deliver_event(client, email_id, event="email.delivered"):
    return client.post(
        WEBHOOK_URL,
        headers=WEBHOOK_HEADERS,
        json={"type": event, "data": {"email_id": email_id}},
    )


def test_sos_requires_auth(client):
    assert client.post("/api/emergency/sos", json={}).status_code == 401


def test_sos_without_verified_contacts_is_409(client, unique_user):
    _, headers = unique_user
    # An unverified contact is NOT eligible.
    add_contact(client, headers, "Mum", "unverified@example.com")
    r = client.post("/api/emergency/sos", headers=headers, json={"incident_type": "Fall"})
    assert r.status_code == 409
    assert "verif" in r.json()["detail"].lower()


def test_sos_creates_incident_and_queued_jobs(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "mum@example.com")
    add_verified_contact(client, headers, "Dad", "dad@example.com")

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
    assert all(n["channel"] == "email" for n in body["notifications"])
    # Before any worker run, everything is QUEUED — never claimed as sent.
    assert all(n["status"] == "queued" for n in body["notifications"])
    assert body["notification_summary"]["overall"] == "processing"


def test_missing_location_still_queues(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "noloc@example.com")
    r = client.post("/api/emergency/sos", headers=headers, json={"incident_type": "Medical"})
    assert r.status_code == 200
    assert r.json()["location_available"] is False
    assert len(r.json()["notifications"]) == 1


def test_idempotent_sos_does_not_double_queue(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "idem@example.com")
    payload = {"incident_type": "Fire", "idempotency_key": "abc-123"}
    first = client.post("/api/emergency/sos", headers=headers, json=payload).json()
    second = client.post("/api/emergency/sos", headers=headers, json=payload).json()
    assert first["incident_id"] == second["incident_id"]
    assert second["created"] is False
    assert len(second["notifications"]) == len(first["notifications"]) == 1


def test_worker_marks_accepted_not_delivered_then_webhook_delivers(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "accept@example.com")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]

    drain_pending()  # process the outbox once (mock accepts)

    status = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()
    n = status["notifications"][0]
    assert n["status"] == "accepted_by_provider"  # accepted != delivered
    assert n["provider_message_id"]
    assert status["notification_summary"]["overall"] == "provider_accepted"

    mid = n["provider_message_id"]
    cb = _deliver_event(client, mid)
    assert cb.status_code == 200 and cb.json()["applied"] == 1

    after = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()
    assert after["notifications"][0]["status"] == "delivered"
    assert after["notifications"][0]["delivered_at"]
    assert after["notification_summary"]["overall"] == "all_delivered"


def test_duplicate_and_terminal_webhooks_do_not_regress(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "noregress@example.com")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    drain_pending()
    mid = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()[
        "notifications"][0]["provider_message_id"]

    _deliver_event(client, mid, "email.delivered")
    _deliver_event(client, mid, "email.bounced")     # late/out-of-order: must NOT regress
    _deliver_event(client, mid, "email.delivered")   # duplicate: harmless

    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "delivered"


def test_bounce_marks_bounced_and_suppresses_contact(client, unique_user):
    _, headers = unique_user
    cid = add_verified_contact(client, headers, "Bouncy", "bounce-target@example.com")["id"]
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    mid = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()[
        "notifications"][0]["provider_message_id"]

    assert _deliver_event(client, mid, "email.bounced").status_code == 200
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "bounced"
    # The contact is now suppressed (won't be alerted again until re-verified).
    contact = next(c for c in client.get("/api/contacts", headers=headers).json() if c["id"] == cid)
    assert contact["suppressed"] is True


def test_forged_webhook_rejected(client):
    # No secret header at all.
    assert client.post(
        WEBHOOK_URL, json={"type": "email.delivered", "data": {"email_id": "x"}}
    ).status_code == 401
    # Wrong secret.
    assert client.post(
        WEBHOOK_URL,
        headers={"x-webhook-secret": "WRONG"},
        json={"type": "email.delivered", "data": {"email_id": "x"}},
    ).status_code == 401


def test_independent_recipients_one_permanent_fail(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Good", "good@example.com")
    add_verified_contact(client, headers, "Bad", "permanent@example.com")  # mock -> permanent fail
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    notifs = {n["recipient_email"]: n for n in
              client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"]}
    assert notifs["good@example.com"]["status"] == "accepted_by_provider"
    assert notifs["permanent@example.com"]["status"] == "failed"  # one bad address didn't block the other


def test_retryable_eventually_fails_after_max_attempts(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Flaky", "retryable@example.com")  # mock -> retryable
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    import time

    for _ in range(6):
        drain_pending()
        time.sleep(0.05)
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "failed"
    assert n["attempts"] >= 1


def test_timeout_outcome_is_unknown_not_retried(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Timeout", "timeout@example.com")  # mock -> UNKNOWN
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "unknown"  # not blindly retried/marked sent


def test_manual_retry_of_failed(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Bad", "permanent@example.com")  # permanent fail
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fire"}
    ).json()["incident_id"]
    drain_pending()
    nid = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()[
        "notifications"][0]["id"]
    r = client.post(f"/api/emergency/notifications/{nid}/retry", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "queued"


def test_cannot_read_another_users_incident(client, unique_user, auth_other):
    _, headers = unique_user
    _, other = auth_other
    add_verified_contact(client, headers, "Mum", "isolate@example.com")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    assert client.get(f"/api/emergency/incidents/{inc_id}", headers=other).status_code == 404


def test_cancel_incident_stops_queued(client, unique_user):
    _, headers = unique_user
    add_verified_contact(client, headers, "Mum", "cancel@example.com")
    inc_id = client.post(
        "/api/emergency/sos", headers=headers, json={"incident_type": "Fall"}
    ).json()["incident_id"]
    r = client.post(f"/api/emergency/incidents/{inc_id}/cancel", headers=headers)
    assert r.status_code == 200
    assert r.json()["status"] == "CANCELLED"
    # Queued jobs are stopped (marked cancelled) before any send.
    n = client.get(f"/api/emergency/incidents/{inc_id}", headers=headers).json()["notifications"][0]
    assert n["status"] == "cancelled"


def test_email_health_reports_mock(client):
    h = client.get("/api/email/health").json()
    assert h["provider"] == "mock"
    assert h["isMock"] is True
    assert h["realSendingConfigured"] is False
