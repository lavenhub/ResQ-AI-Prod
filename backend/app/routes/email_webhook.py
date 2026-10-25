"""Provider delivery-event webhook (Resend, signed with Svix).

The email provider POSTs here when an email is sent/delivered/deferred/bounced/
complained/failed. We:
  * authenticate the request by verifying the Svix signature over the RAW body
    (svix-id / svix-timestamp / svix-signature) against EMAIL_WEBHOOK_SECRET,
    with a timestamp tolerance to blunt replays;
  * parse the provider-specific body via the provider abstraction;
  * apply each update idempotently + monotonically (never regress a terminal
    DELIVERED/BOUNCED/FAILED on a duplicate/out-of-order event), recording every
    event for audit and suppressing contacts on hard bounce / spam complaint;
  * return the small 200 the provider expects, exposing no sensitive data.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlmodel import Session

from ..db import get_session
from ..services.email import get_provider
from ..services.notifier import apply_delivery_update

router = APIRouter(prefix="/api/email")


@router.post("/webhook")
async def delivery_webhook(
    request: Request,
    session: Session = Depends(get_session),
) -> dict:
    provider = get_provider()
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}

    # Authenticate BEFORE parsing/acting on the body.
    if not provider.verify_webhook(raw_body=raw, headers=headers):
        # Do not reveal why; just refuse (401 distinguishes it from a parse 400).
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="unexpected payload") from None

    updates = provider.parse_webhook_event(payload, headers)

    applied = 0
    unmatched = 0
    for upd in updates:
        if apply_delivery_update(
            session,
            upd.provider_message_id,
            upd.status,
            upd.detail,
            event_type=upd.event_type,
            is_bounce=upd.is_bounce,
            is_complaint=upd.is_complaint,
        ):
            applied += 1
        else:
            unmatched += 1

    logger.info(f"[email-webhook] applied={applied} unmatched={unmatched}")
    return {"ok": True, "applied": applied}
