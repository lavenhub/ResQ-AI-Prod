"""Ambulance/hospital directory CRUD + verification tests."""

from __future__ import annotations

from tests.helpers import create_service


def test_directory_requires_auth(client):
    assert client.get("/api/ambulance/services").status_code == 401
    assert client.post("/api/ambulance/services", json={"name": "X", "phone": "+919812345678"}).status_code == 401


def test_create_lists_and_masks_number(client, unique_user):
    _, headers = unique_user
    svc = create_service(client, headers, name="Apollo", phone="+919812345000")
    assert svc["verified"] is False  # verification is an explicit action
    assert svc["is_demo"] is False
    # The full number is never exposed — it is masked.
    assert "*" in svc["phone"]
    assert "9812345000" not in svc["phone"]

    rows = client.get("/api/ambulance/services", headers=headers).json()
    assert any(r["id"] == svc["id"] for r in rows)


def test_invalid_phone_rejected(client, unique_user):
    _, headers = unique_user
    r = client.post("/api/ambulance/services", headers=headers, json={"name": "Bad", "phone": "12"})
    assert r.status_code == 422


def test_verify_and_update_and_delete(client, unique_user):
    _, headers = unique_user
    svc = create_service(client, headers, name="Fortis", phone="+919812345111")

    rv = client.post(f"/api/ambulance/services/{svc['id']}/verify", headers=headers)
    assert rv.status_code == 200 and rv.json()["verified"] is True

    rp = client.patch(f"/api/ambulance/services/{svc['id']}", headers=headers,
                       json={"active": False, "capabilities": "ICU"})
    assert rp.status_code == 200
    assert rp.json()["active"] is False

    # Deactivated service is hidden from the default list but visible with include_inactive.
    default = client.get("/api/ambulance/services", headers=headers).json()
    assert all(r["id"] != svc["id"] for r in default)
    allrows = client.get("/api/ambulance/services?include_inactive=true", headers=headers).json()
    assert any(r["id"] == svc["id"] for r in allrows)

    rd = client.delete(f"/api/ambulance/services/{svc['id']}", headers=headers)
    assert rd.status_code == 204
    assert client.patch(f"/api/ambulance/services/{svc['id']}", headers=headers,
                        json={"active": True}).status_code == 404


def test_demo_service_flagged(client, unique_user):
    _, headers = unique_user
    svc = create_service(client, headers, name="Demo Reception", phone="+919812345222", is_demo=True)
    assert svc["is_demo"] is True
