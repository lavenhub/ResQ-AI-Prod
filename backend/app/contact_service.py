"""Emergency-contact business logic — the one validated path for contact data.

Every function is scoped to an authenticated ``user_ref`` (the owner's E.164
phone from the session). A user can only ever read/write their own contacts, and
phone numbers are normalized to E.164 + de-duplicated here so the SMS pipeline
always receives canonical, valid numbers.
"""

from __future__ import annotations

import re

from sqlmodel import Session, select

from .config import get_settings
from .models import EmergencyContact, ContactCreate, ContactUpdate, utcnow
from .services.phone import PhoneValidationError, normalize_phone


class ContactError(ValueError):
    """Raised for validation/limit/duplicate problems (surfaced as 4xx)."""


# Pragmatic email check (full RFC validation is not worth a dependency here).
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    if not _EMAIL_RE.match(text):
        raise ContactError("Enter a valid email address.")
    return text.lower()


def list_contacts(session: Session, user_ref: str) -> list[EmergencyContact]:
    stmt = (
        select(EmergencyContact)
        .where(EmergencyContact.user_ref == user_ref)
        .order_by(EmergencyContact.priority, EmergencyContact.created_at)  # type: ignore[arg-type]
    )
    return list(session.exec(stmt).all())


def active_contacts(session: Session, user_ref: str) -> list[EmergencyContact]:
    """Contacts eligible to receive SOS alerts, in contact priority order."""
    return [c for c in list_contacts(session, user_ref) if c.active]


def get_contact(session: Session, user_ref: str, contact_id: str) -> EmergencyContact | None:
    contact = session.get(EmergencyContact, contact_id)
    # Ownership check: never return another user's contact.
    if contact is None or contact.user_ref != user_ref:
        return None
    return contact


def _normalize(raw: str, country: str | None) -> tuple[str, str]:
    settings = get_settings()
    try:
        norm = normalize_phone(raw, default_region=country or settings.default_country)
    except PhoneValidationError as exc:
        raise ContactError(str(exc)) from exc
    return norm.e164, norm.country


def create_contact(session: Session, user_ref: str, data: ContactCreate) -> EmergencyContact:
    settings = get_settings()
    e164, country = _normalize(data.phone, data.country)

    existing = list_contacts(session, user_ref)
    if len(existing) >= settings.max_contacts_per_user:
        raise ContactError(
            f"Contact limit reached ({settings.max_contacts_per_user}). "
            "Remove one before adding another."
        )
    if any(c.phone_e164 == e164 for c in existing):
        raise ContactError("A contact with this phone number already exists.")

    contact = EmergencyContact(
        user_ref=user_ref,
        name=data.name,
        phone_e164=e164,
        email=_normalize_email(data.email),
        country=country,
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
    if "phone" in changes and changes["phone"] is not None:
        e164, country = _normalize(changes["phone"], changes.get("country") or contact.country)
        # Enforce uniqueness against the user's OTHER contacts.
        for c in list_contacts(session, user_ref):
            if c.id != contact.id and c.phone_e164 == e164:
                raise ContactError("A contact with this phone number already exists.")
        contact.phone_e164 = e164
        contact.country = country
        # Changing the number invalidates any prior verification.
        contact.verified = False
    if changes.get("name") is not None:
        if not str(changes["name"]).strip():
            raise ContactError("name is required")
        contact.name = str(changes["name"]).strip()
    if "email" in changes:
        contact.email = _normalize_email(changes["email"])
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
