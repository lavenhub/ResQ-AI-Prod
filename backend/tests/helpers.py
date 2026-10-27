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
