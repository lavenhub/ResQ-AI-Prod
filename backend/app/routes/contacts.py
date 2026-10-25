"""Emergency-email-contact management API (authenticated, per-user).

Every write route requires a valid session token and operates ONLY on the
caller's own contacts. Email addresses are normalized + de-duplicated server-side.
Verification is a real, token-based email-ownership check; the public ``/verify``
endpoint is intentionally unauthenticated (the single-use token IS the credential).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.responses import HTMLResponse
from loguru import logger
from sqlmodel import Session

from .. import contact_service
from ..auth import get_current_user
from ..config import get_settings
from ..contact_service import ContactError
from ..db import get_session
from ..models import ContactCreate, ContactRead, ContactUpdate, VerificationStartResponse
from ..services.email import get_provider
from ..services.email.base import OutboundEmail
from ..services.email.templating import render_verification_email
from ..services.phone import mask_phone
from ..services.ratelimit import contacts_limiter
from ..services.verification import build_verify_url

router = APIRouter(prefix="/api/contacts")


def _rate_limit(user_ref: str) -> None:
    s = get_settings()
    if not contacts_limiter.check(
        user_ref, limit=s.contacts_rate_limit, window_seconds=s.contacts_rate_window_seconds
    ):
        raise HTTPException(status_code=429, detail="Too many contact changes; slow down.")


@router.get("", response_model=list[ContactRead])
async def list_my_contacts(
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[ContactRead]:
    rows = contact_service.list_contacts(session, user_ref)
    return [ContactRead.model_validate(r) for r in rows]


@router.post("", response_model=ContactRead, status_code=status.HTTP_201_CREATED)
async def create_my_contact(
    body: ContactCreate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ContactRead:
    _rate_limit(user_ref)
    try:
        contact = contact_service.create_contact(session, user_ref, body)
    except ContactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ContactRead.model_validate(contact)


@router.patch("/{contact_id}", response_model=ContactRead)
async def update_my_contact(
    contact_id: str,
    body: ContactUpdate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ContactRead:
    _rate_limit(user_ref)
    try:
        contact = contact_service.update_contact(session, user_ref, contact_id, body)
    except ContactError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if contact is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    return ContactRead.model_validate(contact)


@router.delete("/{contact_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_my_contact(
    contact_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    _rate_limit(user_ref)
    ok = contact_service.delete_contact(session, user_ref, contact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Contact not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{contact_id}/send-verification", response_model=VerificationStartResponse)
async def send_verification(
    contact_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> VerificationStartResponse:
    """(Re)send a single-use verification email to a contact's address.

    The verification link only ever reaches the recipient's inbox with a real
    provider. With the mock provider (dev/tests) the link is returned in
    ``dev_verification_url`` so the loop can be completed without a live mailbox.
    """
    _rate_limit(user_ref)
    started = contact_service.start_verification(session, user_ref, contact_id)
    if started is None:
        raise HTTPException(status_code=404, detail="Contact not found")
    contact, token = started
    verify_url = build_verify_url(token)

    settings = get_settings()
    provider = get_provider()
    mail = render_verification_email(
        brand=settings.email_brand_name,
        contact_name=contact.name,
        owner_label=f"The ResQAI user {mask_phone(user_ref)}",
        verify_url=verify_url,
    )
    result = await provider.send(
        OutboundEmail(
            to_email=contact.email,
            subject=mail.subject,
            text=mail.text,
            html=mail.html,
            idempotency_key=f"verify-{contact.id}-{contact.verification_sent_at}",
            tags={"type": "verification"},
        )
    )
    sent = result.outcome.value == "accepted"
    if not sent:
        logger.warning(f"[contacts] verification send not accepted: {result.detail}")
    return VerificationStartResponse(
        contact_id=contact.id,
        sent=sent,
        # Only expose the link for a non-real provider (dev/demo), never for real sends.
        dev_verification_url=verify_url if provider.is_mock else None,
        detail=None if sent else (result.detail or "could not send verification email"),
    )


@router.get("/verify", response_class=HTMLResponse)
async def verify_contact_email(
    token: str = "",
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Public, single-use email-verification landing page.

    Unauthenticated by design: the opaque token is the credential. Returns a
    minimal self-contained HTML confirmation (no PII, no tracking).
    """
    contact = contact_service.verify_email_token(session, token) if token else None
    if contact is None:
        return HTMLResponse(_verify_page(ok=False), status_code=400)
    return HTMLResponse(_verify_page(ok=True, name=contact.name))


def _verify_page(*, ok: bool, name: str | None = None) -> str:
    from html import escape

    brand = get_settings().email_brand_name
    if ok:
        title = "Email confirmed"
        body = (
            f"<p>Thank you{', ' + escape(name) if name else ''}. Your email is now "
            f"confirmed as a {escape(brand)} emergency contact.</p>"
            "<p>You may receive emergency alerts for the person who added you. These "
            "are automated notifications — they do not mean emergency services have "
            "been dispatched.</p>"
        )
        color = "#1b7a3d"
    else:
        title = "Link invalid or expired"
        body = (
            "<p>This verification link is invalid, has expired, or has already been "
            "used. Ask the person who added you to send a new confirmation link.</p>"
        )
        color = "#C62828"
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{escape(title)}</title></head>"
        '<body style="margin:0;background:#f2f2f2;font:16px/1.6 Arial,sans-serif;color:#111;">'
        '<div style="max-width:520px;margin:48px auto;background:#fff;border:2px solid #000;">'
        f'<div style="background:{color};color:#fff;padding:16px 20px;font-weight:bold;'
        f'font-size:18px;">{escape(brand)} — {escape(title)}</div>'
        f'<div style="padding:20px;">{body}</div></div></body></html>'
    )
