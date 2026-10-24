"""Emergency-contact management API (authenticated, per-user).

Every route requires a valid session token and operates ONLY on the caller's own
contacts. Phone numbers are normalized to E.164 and de-duplicated server-side.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlmodel import Session

from .. import contact_service
from ..auth import get_current_user
from ..config import get_settings
from ..contact_service import ContactError
from ..db import get_session
from ..models import ContactCreate, ContactRead, ContactUpdate
from ..services.ratelimit import contacts_limiter

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
