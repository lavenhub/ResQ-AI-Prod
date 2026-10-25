"""Email-ownership verification tokens.

A verification token proves the person at a contact's email address consents to
being an emergency contact. Properties:
  * cryptographically random, single-use, and time-limited
  * only a SHA-256 hash is ever stored (a DB leak cannot be replayed as a link)
  * verification happens on the backend; saving a contact never implies it

The token is opaque (no contact id embedded); the backend finds the contact by
matching the stored hash, so a token reveals nothing about which record it is.
"""

from __future__ import annotations

import hashlib
import secrets
from urllib.parse import quote

from ..config import get_settings


def generate_token() -> str:
    """A URL-safe, high-entropy single-use token."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest — what we persist (never the raw token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_verify_url(token: str) -> str:
    """The public link the recipient clicks to confirm their email."""
    base = get_settings().public_app_url.rstrip("/")
    return f"{base}/api/contacts/verify?token={quote(token, safe='')}"
