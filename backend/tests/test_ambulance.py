"""Ambulance local simulated-call workflow tests (Gemini mocked — no network)."""

from __future__ import annotations

import app.routes.ambulance as amb


def _auth(client):
    body = client.post("/api/auth/verify-otp", json={"phone": "+919500000007", "code": "123456"}).json()
    return {"Authorization": f"Bearer {body['token']}"}


def test_services_seeded_and_listed(client):
    headers = _auth(client)
    r = client.get("/api/ambulance/services", headers=headers)
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) >= 1
    # Numbers are masked, never raw.
    assert all("*" in s["phone"] for s in rows)


def test_create_booking_and_requires_auth(client):
    assert client.get("/api/ambulance/services").status_code == 401
    headers = _auth(client)
    svc = client.get("/api/ambulance/services", headers=headers).json()[0]
    r = client.post("/api/ambulance/bookings", headers=headers, json={
        "service_id": svc["id"], "emergency_type": "Road Accident",
        "patient_condition": "leg fracture, conscious",
        "latitude": 19.07, "longitude": 72.87, "address": "MG Road, Mumbai",
    })
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["booking_state"] == "PREPARING_REQUEST"
    assert b["provider"] == "simulated"
    assert b["pickup_address"] == "MG Road, Mumbai"


def test_full_call_flow_confirmed(client, monkeypatch):
    headers = _auth(client)
    svc = client.get("/api/ambulance/services", headers=headers).json()[0]
    b = client.post("/api/ambulance/bookings", headers=headers, json={
        "service_id": svc["id"], "emergency_type": "Snakebite",
        "patient_condition": "snakebite on leg", "latitude": 18.9, "longitude": 72.8,
        "address": "Marine Drive",
    }).json()
    bid = b["id"]

    async def _fake_open(ctx, transcript):
        # Coordinator must have the verified facts to share.
        assert ctx["pickup_address"] == "Marine Drive"
        assert "snakebite" in (ctx["patient_condition"] or "").lower()
        return "Hello, this is ResQ, an automated assistant. We have a snakebite patient at Marine Drive. Is an ambulance available?"

    monkeypatch.setattr(amb, "coordinator_reply", _fake_open)

    ro = client.post(f"/api/ambulance/bookings/{bid}/call/open", headers=headers)
    assert ro.status_code == 200, ro.text
    assert ro.json()["booking"]["booking_state"] == "CONNECTED"
    assert "ResQ" in ro.json()["reply"]

    # A turn (receptionist replied).
    rt = client.post(f"/api/ambulance/bookings/{bid}/call/turn", headers=headers, json={
        "transcript": [
            {"role": "ai", "text": "Is an ambulance available?"},
            {"role": "receptionist", "text": "Yes, ICU ambulance, 12 minutes, 1500 rupees."},
        ]
    })
    assert rt.status_code == 200

    async def _fake_outcome(transcript):
        return {
            "availability": "AVAILABLE", "ambulance_type": "ICU", "eta": "12 minutes",
            "charges": "Rs 1500", "booking_reference": "BK-42",
            "booking_confirmed": True, "declined": False, "summary": "Confirmed ICU ambulance, 12 min.",
        }

    monkeypatch.setattr(amb, "extract_outcome", _fake_outcome)
    rf = client.post(f"/api/ambulance/bookings/{bid}/call/finalize", headers=headers, json={"transcript": []})
    assert rf.status_code == 200, rf.text
    out = rf.json()
    assert out["booking_state"] == "BOOKING_CONFIRMED"
    assert out["availability"] == "AVAILABLE"
    assert out["eta_text"] == "12 minutes"
    assert out["charges_text"] == "Rs 1500"
    assert out["booking_reference"] == "BK-42"


def test_call_flow_declined(client, monkeypatch):
    headers = _auth(client)
    svc = client.get("/api/ambulance/services", headers=headers).json()[0]
    b = client.post("/api/ambulance/bookings", headers=headers, json={"service_id": svc["id"]}).json()

    async def _fake_outcome(transcript):
        return {
            "availability": "UNAVAILABLE", "ambulance_type": None, "eta": None, "charges": None,
            "booking_reference": None, "booking_confirmed": False, "declined": True,
            "summary": "No ambulance available.",
        }

    monkeypatch.setattr(amb, "extract_outcome", _fake_outcome)
    rf = client.post(f"/api/ambulance/bookings/{b['id']}/call/finalize", headers=headers, json={"transcript": []})
    assert rf.json()["booking_state"] == "BOOKING_DECLINED"


def test_cannot_access_other_users_booking(client, monkeypatch):
    headers = _auth(client)
    svc = client.get("/api/ambulance/services", headers=headers).json()[0]
    b = client.post("/api/ambulance/bookings", headers=headers, json={"service_id": svc["id"]}).json()
    other = {"Authorization": f"Bearer {client.post('/api/auth/verify-otp', json={'phone': '+919500000008', 'code': '123456'}).json()['token']}"}
    assert client.get(f"/api/ambulance/bookings/{b['id']}", headers=other).status_code == 404
