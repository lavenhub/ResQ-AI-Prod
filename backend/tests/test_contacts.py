"""Integration tests: emergency email-contact management + verification (authz)."""

from __future__ import annotations

from tests.helpers import add_contact


def test_requires_auth(client):
    assert client.get("/api/contacts").status_code == 401
    assert client.post(
        "/api/contacts", json={"name": "X", "email": "x@example.com"}
    ).status_code == 401


def test_create_list_normalizes_email(client, auth):
    _, headers = auth
    r = client.post(
        "/api/contacts",
        headers=headers,
        json={"name": "Mum", "email": "MUM@Example.com", "relationship": "mother"},
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "mum@example.com"  # normalized lowercase
    assert body["verified"] is False  # saving never implies verification
    assert body["suppressed"] is False

    lst = client.get("/api/contacts", headers=headers).json()
    assert any(c["email"] == "mum@example.com" for c in lst)


def test_rejects_invalid_email(client, auth):
    _, headers = auth
    r = client.post("/api/contacts", headers=headers, json={"name": "X", "email": "nope"})
    assert r.status_code == 422


def test_duplicate_email_rejected(client, auth):
    _, headers = auth
    client.post("/api/contacts", headers=headers, json={"name": "A", "email": "dup@example.com"})
    r = client.post("/api/contacts", headers=headers, json={"name": "B", "email": "DUP@example.com"})
    assert r.status_code == 422
    assert "already exists" in r.json()["detail"].lower()


def test_cannot_touch_another_users_contact(client, auth, auth_other):
    _, headers = auth
    _, other_headers = auth_other
    created = client.post(
        "/api/contacts", headers=headers, json={"name": "Secret", "email": "secret@example.com"}
    ).json()
    cid = created["id"]

    other_list = client.get("/api/contacts", headers=other_headers).json()
    assert all(c["id"] != cid for c in other_list)
    assert client.patch(
        f"/api/contacts/{cid}", headers=other_headers, json={"name": "Hacked"}
    ).status_code == 404
    assert client.delete(f"/api/contacts/{cid}", headers=other_headers).status_code == 404
    # Another user cannot send verification for someone else's contact either.
    assert client.post(
        f"/api/contacts/{cid}/send-verification", headers=other_headers
    ).status_code == 404
    assert client.delete(f"/api/contacts/{cid}", headers=headers).status_code == 204


def test_update_and_deactivate(client, unique_user):
    _, headers = unique_user
    cid = client.post(
        "/api/contacts", headers=headers, json={"name": "Dad", "email": "dad@example.com"}
    ).json()["id"]
    r = client.patch(f"/api/contacts/{cid}", headers=headers, json={"active": False, "priority": 1})
    assert r.status_code == 200
    assert r.json()["active"] is False
    assert r.json()["priority"] == 1


def test_verification_loop_marks_verified(client, unique_user):
    _, headers = unique_user
    cid = add_contact(client, headers, "Mum", "verify-me@example.com")["id"]

    r = client.post(f"/api/contacts/{cid}/send-verification", headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sent"] is True
    url = body["dev_verification_url"]
    assert url and "/api/contacts/verify?token=" in url

    # Still unverified until the link is visited.
    assert client.get("/api/contacts", headers=headers).json()[0]["verified"] is False

    path = url.replace("http://testserver", "")
    page = client.get(path)
    assert page.status_code == 200
    assert "confirmed" in page.text.lower()

    assert client.get("/api/contacts", headers=headers).json()[0]["verified"] is True


def test_verification_token_is_single_use(client, unique_user):
    _, headers = unique_user
    cid = add_contact(client, headers, "Mum", "single-use@example.com")["id"]
    url = client.post(
        f"/api/contacts/{cid}/send-verification", headers=headers
    ).json()["dev_verification_url"]
    path = url.replace("http://testserver", "")
    assert client.get(path).status_code == 200
    # Re-using the same link fails (token consumed).
    assert client.get(path).status_code == 400


def test_verify_with_garbage_token_is_400(client):
    assert client.get("/api/contacts/verify?token=not-a-real-token").status_code == 400
    assert client.get("/api/contacts/verify").status_code == 400


def test_changing_email_invalidates_verification(client, unique_user):
    _, headers = unique_user
    cid = add_contact(client, headers, "Mum", "before@example.com")["id"]
    url = client.post(
        f"/api/contacts/{cid}/send-verification", headers=headers
    ).json()["dev_verification_url"]
    client.get(url.replace("http://testserver", ""))
    assert client.get("/api/contacts", headers=headers).json()[0]["verified"] is True
    # Editing the address drops verification.
    r = client.patch(f"/api/contacts/{cid}", headers=headers, json={"email": "after@example.com"})
    assert r.status_code == 200
    assert r.json()["verified"] is False


def test_contact_limit_enforced(client, unique_user):
    _, headers = unique_user
    from app.config import get_settings

    limit = get_settings().max_contacts_per_user
    for i in range(limit):
        rr = client.post(
            "/api/contacts", headers=headers, json={"name": f"C{i}", "email": f"c{i}@example.com"}
        )
        assert rr.status_code == 201, rr.text
    over = client.post(
        "/api/contacts", headers=headers, json={"name": "Over", "email": "over@example.com"}
    )
    assert over.status_code == 422
    assert "limit" in over.json()["detail"].lower()
