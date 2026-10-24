"""Integration tests: emergency-contact management (auth + per-user authz)."""

from __future__ import annotations


def test_requires_auth(client):
    assert client.get("/api/contacts").status_code == 401
    assert client.post("/api/contacts", json={"name": "X", "phone": "+919876543210"}).status_code == 401


def test_create_list_normalizes_e164(client, auth):
    _, headers = auth
    r = client.post(
        "/api/contacts",
        headers=headers,
        json={"name": "Mum", "phone": "98765 43210", "country": "IN", "relationship": "mother"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["phone_e164"] == "+919876543210"
    assert body["verified"] is False  # saving never implies verification

    lst = client.get("/api/contacts", headers=headers).json()
    assert any(c["phone_e164"] == "+919876543210" for c in lst)


def test_rejects_invalid_phone(client, auth):
    _, headers = auth
    r = client.post("/api/contacts", headers=headers, json={"name": "X", "phone": "nope"})
    assert r.status_code == 422


def test_duplicate_phone_rejected(client, auth):
    _, headers = auth
    client.post("/api/contacts", headers=headers, json={"name": "A", "phone": "+919812345678"})
    r = client.post("/api/contacts", headers=headers, json={"name": "B", "phone": "+91 98123 45678"})
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"].lower()


def test_cannot_touch_another_users_contact(client, auth, auth_other):
    _, headers = auth
    _, other_headers = auth_other
    created = client.post(
        "/api/contacts", headers=headers, json={"name": "Secret", "phone": "+919700000001"}
    ).json()
    cid = created["id"]

    # The other user cannot see it in their list...
    other_list = client.get("/api/contacts", headers=other_headers).json()
    assert all(c["id"] != cid for c in other_list)
    # ...nor update it...
    assert client.patch(f"/api/contacts/{cid}", headers=other_headers, json={"name": "Hacked"}).status_code == 404
    # ...nor delete it.
    assert client.delete(f"/api/contacts/{cid}", headers=other_headers).status_code == 404
    # The owner still can.
    assert client.delete(f"/api/contacts/{cid}", headers=headers).status_code == 204


def test_update_and_deactivate(client, auth):
    _, headers = auth
    cid = client.post(
        "/api/contacts", headers=headers, json={"name": "Dad", "phone": "+919700000009"}
    ).json()["id"]
    r = client.patch(f"/api/contacts/{cid}", headers=headers, json={"active": False, "priority": 1})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["priority"] == 1


def test_contact_limit_enforced(client, auth):
    _, headers = auth
    # Fill up to the limit with unique valid numbers.
    from app.config import get_settings

    limit = get_settings().max_contacts_per_user
    # Clear any existing first.
    for c in client.get("/api/contacts", headers=headers).json():
        client.delete(f"/api/contacts/{c['id']}", headers=headers)
    for i in range(limit):
        num = f"+9198200000{i:02d}"
        rr = client.post("/api/contacts", headers=headers, json={"name": f"C{i}", "phone": num})
        assert rr.status_code == 201, rr.text
    over = client.post("/api/contacts", headers=headers, json={"name": "Over", "phone": "+919899999999"})
    assert over.status_code == 422
    assert "limit" in over.json()["detail"].lower()
