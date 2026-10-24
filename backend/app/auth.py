"""Signed session tokens + the authenticated-user dependency.

ResQAI's login is a (demo) phone-OTP. On successful verify we issue a compact,
HMAC-signed bearer token whose subject is the caller's **E.164 phone number** —
that string is the user's identity (``user_ref``) everywhere else in the backend.

The token is a real, verifiable credential (stateless HMAC, like a minimal JWT):
it is the thing that lets the SOS and contact endpoints authorize *this* user and
refuse to read or trigger anyone else's data. It is NOT a claim that the OTP step
itself is secure — that stays a documented demo until a real SMS OTP is wired.

Design notes:
  * stdlib-only (hmac/hashlib/base64) — no extra dependency, no key material in
    client code.
  * Secret comes from ``AUTH_SECRET``; absent => an ephemeral per-process secret
    with a loud warning (dev convenience only; tokens die on restart).
  * Constant-time signature comparison; explicit expiry check.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from functools import lru_cache

from fastapi import Depends, Header, HTTPException
from loguru import logger

from .config import get_settings


@lru_cache
def _signing_secret() -> bytes:
    configured = get_settings().auth_secret
    if configured:
        return configured.encode("utf-8")
    # Dev fallback: ephemeral secret. Tokens will not survive a restart and are
    # not shared across processes — never rely on this in production.
    logger.warning(
        "AUTH_SECRET is not set — using an ephemeral per-process signing secret. "
        "Set AUTH_SECRET in the environment for stable, multi-process sessions."
    )
    return secrets.token_bytes(32)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_token(user_ref: str, *, ttl_seconds: int | None = None) -> str:
    """Create a signed bearer token for ``user_ref`` (an E.164 phone)."""
    settings = get_settings()
    ttl = settings.auth_token_ttl_seconds if ttl_seconds is None else ttl_seconds
    now = int(time.time())
    payload = {"sub": user_ref, "iat": now, "exp": now + ttl}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    sig = _b64url(hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


class TokenError(ValueError):
    pass


def verify_token(token: str) -> str:
    """Return the ``user_ref`` encoded in a valid, unexpired token.

    Raises ``TokenError`` on any tampering, malformed input, or expiry.
    """
    if not token or token.count(".") != 1:
        raise TokenError("malformed token")
    body, sig = token.split(".", 1)
    expected = _b64url(
        hmac.new(_signing_secret(), body.encode("ascii"), hashlib.sha256).digest()
    )
    # Constant-time comparison defeats timing attacks on the signature.
    if not hmac.compare_digest(sig, expected):
        raise TokenError("bad signature")
    try:
        payload = json.loads(_b64url_decode(body))
    except Exception as exc:  # noqa: BLE001
        raise TokenError("undecodable payload") from exc
    if not isinstance(payload, dict) or "sub" not in payload or "exp" not in payload:
        raise TokenError("missing claims")
    if int(payload["exp"]) < int(time.time()):
        raise TokenError("expired")
    sub = str(payload["sub"])
    if not sub:
        raise TokenError("empty subject")
    return sub


# ── FastAPI dependencies ──────────────────────────────────────────────────────
def _extract_bearer(authorization: str | None) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    return parts[1].strip()


def get_current_user(authorization: str | None = Header(default=None)) -> str:
    """FastAPI dependency: returns the authenticated user's E.164 phone (user_ref).

    401 on any missing/invalid/expired token. Use this on every endpoint that
    touches per-user data.
    """
    token = _extract_bearer(authorization)
    try:
        return verify_token(token)
    except TokenError as exc:
        raise HTTPException(status_code=401, detail=f"Invalid session: {exc}") from exc


def get_optional_user(authorization: str | None = Header(default=None)) -> str | None:
    """Like ``get_current_user`` but returns None instead of raising when absent.

    Used by the WebRTC offer, which may be opened before the token is attached.
    """
    if not authorization:
        return None
    try:
        return verify_token(_extract_bearer(authorization))
    except (HTTPException, TokenError):
        return None


# Convenience alias for route signatures.
CurrentUser = Depends(get_current_user)
