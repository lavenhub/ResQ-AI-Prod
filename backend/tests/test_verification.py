"""Unit tests for verification token helpers + service-level expiry/single-use."""

from __future__ import annotations

from datetime import timedelta

from app.contact_service import create_contact, start_verification, verify_email_token
from app.db import session_scope
from app.models import ContactCreate, utcnow
from app.services import verification


def test_token_helpers():
    t = verification.generate_token()
    assert len(t) >= 32
    # Hash is deterministic + hex sha256.
    assert verification.hash_token(t) == verification.hash_token(t)
    assert len(verification.hash_token(t)) == 64
    url = verification.build_verify_url(t)
    assert "/api/contacts/verify?token=" in url


def _make_contact(user_ref: str, email: str) -> str:
    with session_scope() as s:
        c = create_contact(s, user_ref, ContactCreate(name="X", email=email))
        return c.id


def test_only_hash_is_stored_not_raw_token():
    uid = "+910000000001"
    cid = _make_contact(uid, "hashonly@example.com")
    with session_scope() as s:
        _, token = start_verification(s, uid, cid)
    with session_scope() as s:
        from app.models import EmergencyContact

        c = s.get(EmergencyContact, cid)
        # The raw token must never be persisted; only its hash.
        assert c.verification_token_hash == verification.hash_token(token)
        assert token not in (c.verification_token_hash or "")


def test_expired_token_does_not_verify():
    uid = "+910000000002"
    cid = _make_contact(uid, "expired@example.com")
    with session_scope() as s:
        _, token = start_verification(s, uid, cid)
    # Force expiry into the past.
    with session_scope() as s:
        from app.models import EmergencyContact

        c = s.get(EmergencyContact, cid)
        c.verification_expires_at = utcnow() - timedelta(seconds=1)
        s.add(c)
        s.commit()
    with session_scope() as s:
        assert verify_email_token(s, token) is None
        from app.models import EmergencyContact

        assert s.get(EmergencyContact, cid).verified is False


def test_unknown_token_returns_none():
    with session_scope() as s:
        assert verify_email_token(s, "definitely-not-a-token") is None
