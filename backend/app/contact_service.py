"""Emergency-email-contact business logic — the one validated path for contact data.

Every function is scoped to an authenticated ``user_ref`` (the owner's E.164
phone from the session). A user can only ever read/write their own contacts;
email addresses are normalized + de-duplicated here so the notification pipeline
always receives canonical, valid, consented addresses.

A contact becomes eligible to receive SOS alerts only when it is active AND
verified AND not suppressed — see ``eligible_contacts``.
"""

from __future__ import annotations

import re
from datetime import timedelta

from sqlmodel import Session, select

from .config import get_settings
from .models import ContactCreate, ContactUpdate, EmergencyContact, utcnow
from .services import verification


class ContactError(ValueError):
    """Raised for validation/limit/duplicate problems (surfaced as 4xx)."""


# Pragmatic email check (full RFC validation is not worth a dependency here).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(raw: str | None) -> str:
    if raw is None or not str(raw).strip():
        raise ContactError("Email address is required.")
    text = str(raw).strip()
    if len(text) > 254 or not _EMAIL_RE.match(text):
        raise ContactError("Enter a valid email address.")
    return text.lower()


# ── Reads ──────────────────────────────────────────────────────────────────────
def list_contacts(session: Session, user_ref: str) -> list[EmergencyContact]:
    stmt = (
        select(EmergencyContact)
        .where(EmergencyContact.user_ref == user_ref)
        .order_by(EmergencyContact.priority, EmergencyContact.created_at)  # type: ignore[arg-type]
    )
    return list(session.exec(stmt).all())


def eligible_contacts(session: Session, user_ref: str) -> list[EmergencyContact]:
    """Contacts eligible to receive SOS alerts, in priority order.

    Eligibility requires all of: active (user opted in), verified (the recipient
    confirmed ownership/consent), and not suppressed (no prior bounce/complaint).
    """
    return [
        c
        for c in list_contacts(session, user_ref)
        if c.active and c.verified and not c.suppressed
    ]


def get_contact(session: Session, user_ref: str, contact_id: str) -> EmergencyContact | None:
    contact = session.get(EmergencyContact, contact_id)
    # Ownership check: never return another user's contact.
    if contact is None or contact.user_ref != user_ref:
        return None
    return contact


# ── Writes ───────────────────────────────────────────────────────────────────
def create_contact(session: Session, user_ref: str, data: ContactCreate) -> EmergencyContact:
    settings = get_settings()
    email = normalize_email(data.email)

    existing = list_contacts(session, user_ref)
    if len(existing) >= settings.max_contacts_per_user:
        raise ContactError(
            f"Contact limit reached ({settings.max_contacts_per_user}). "
            "Remove one before adding another."
        )
    if any(c.email == email for c in existing):
        raise ContactError("A contact with this email address already exists.")

    contact = EmergencyContact(
        user_ref=user_ref,
        name=data.name,
        email=email,
        relationship=data.relationship,
        priority=data.priority,
        active=data.active,
        verified=False,  # saving never implies verification
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def update_contact(
    session: Session, user_ref: str, contact_id: str, data: ContactUpdate
) -> EmergencyContact | None:
    contact = get_contact(session, user_ref, contact_id)
    if contact is None:
        return None

    changes = data.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"] is not None:
        email = normalize_email(changes["email"])
        for c in list_contacts(session, user_ref):
            if c.id != contact.id and c.email == email:
                raise ContactError("A contact with this email address already exists.")
        if email != contact.email:
            contact.email = email
            # A new address invalidates any prior verification + suppression.
            contact.verified = False
            contact.verified_at = None
            contact.verification_token_hash = None
            contact.verification_expires_at = None
            contact.suppressed = False
            contact.suppressed_reason = None
    if changes.get("name") is not None:
        if not str(changes["name"]).strip():
            raise ContactError("name is required")
        contact.name = str(changes["name"]).strip()
    if "relationship" in changes:
        contact.relationship = changes["relationship"]
    if changes.get("priority") is not None:
        contact.priority = int(changes["priority"])
    if changes.get("active") is not None:
        contact.active = bool(changes["active"])

    contact.updated_at = utcnow()
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def delete_contact(session: Session, user_ref: str, contact_id: str) -> bool:
    contact = get_contact(session, user_ref, contact_id)
    if contact is None:
        return False
    session.delete(contact)
    session.commit()
    return True


# ── Verification ──────────────────────────────────────────────────────────────
def start_verification(
    session: Session, user_ref: str, contact_id: str
) -> tuple[EmergencyContact, str] | None:
    """Mint a fresh single-use token for a contact and persist only its hash.

    Returns (contact, raw_token). The RAW token is returned so the caller can
    build + send the verification link; it is never stored. Returns None if the
    contact does not belong to the user.
    """
    contact = get_contact(session, user_ref, contact_id)
    if contact is None:
        return None
    token = verification.generate_token()
    now = utcnow()
    contact.verification_token_hash = verification.hash_token(token)
    contact.verification_sent_at = now
    contact.verification_expires_at = now + timedelta(
        seconds=get_settings().email_verification_ttl_seconds
    )
    contact.updated_at = now
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact, token


def verify_email_token(session: Session, token: str) -> EmergencyContact | None:
    """Consume a verification token: mark the contact verified (single-use).

    Returns the contact on success, or None if the token is unknown/expired/used.
    """
    if not token:
        return None
    token_hash = verification.hash_token(token)
    contact = session.exec(
        select(EmergencyContact).where(
            EmergencyContact.verification_token_hash == token_hash
        )
    ).first()
    if contact is None:
        return None
    now = utcnow()
    if contact.verification_expires_at is None or contact.verification_expires_at < now:
        return None  # expired
    contact.verified = True
    contact.verified_at = now
    # Single-use: clear the token so the same link cannot be replayed.
    contact.verification_token_hash = None
    contact.verification_expires_at = None
    # A fresh confirmation clears any stale suppression.
    contact.suppressed = False
    contact.suppressed_reason = None
    contact.updated_at = now
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def suppress_contact(session: Session, contact_id: str | None, reason: str) -> None:
    """Mark a contact suppressed after a hard bounce / spam complaint.

    Suppression stops future sends to an address that rejects us, independently of
    the user's ``active`` preference. No-op if the contact no longer exists.
    """
    if not contact_id:
        return
    contact = session.get(EmergencyContact, contact_id)
    if contact is None:
        return
    contact.suppressed = True
    contact.suppressed_reason = reason[:200] if reason else "bounce/complaint"
    contact.updated_at = utcnow()
    session.add(contact)
    session.commit()
