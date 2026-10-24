"""Provider delivery-report (DLR) webhook.

The SMS provider calls this when a carrier reports a final delivery status. We:
  * authenticate the callback with a shared secret (MSG91 SMS DLR has no HMAC
    signing) presented in the URL as ?secret=... and compared in constant time;
  * parse the provider-specific body via the provider abstraction;
  * apply each update idempotently and monotonically (never regress a terminal
    DELIVERED/FAILED on a duplicate/out-of-order report);
  * return the small 200 the provider expects, exposing no sensitive data.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from loguru import logger
from sqlmodel import Session

from ..db import get_session
from ..services.notifier import apply_delivery_update
from ..services.sms import get_provider

router = APIRouter(prefix="/api/sms")


@router.post("/callback")
async def delivery_callback(
    request: Request,
    secret: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    provider = get_provider()

    # Authenticate BEFORE reading/parsing the body further than necessary.
    if not provider.verify_callback(secret=secret):
        # Do not reveal why; just refuse. (401 so the provider/ops can tell it
        # apart from a parsing 400.)
        raise HTTPException(status_code=401, detail="unauthorized")

    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON") from None

    headers = {k.lower(): v for k, v in request.headers.items()}
    updates = provider.parse_delivery_callback(payload if isinstance(payload, dict) else {"data": payload}, headers)

    applied = 0
    unmatched = 0
    for upd in updates:
        if apply_delivery_update(session, upd.provider_message_id, upd.status, upd.detail):
            applied += 1
        else:
            unmatched += 1

    logger.info(f"[callback] applied={applied} unmatched={unmatched}")
    # Minimal acknowledgement body — no incident/recipient data leaked.
    return {"ok": True, "applied": applied}
