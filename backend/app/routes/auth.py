"""Phone-OTP auth + signed session issuance.

The OTP step is still a DEMO (no SMS is sent; the code is a fixed value matching
what the frontend advertises) — see README "Known limitations". What is NOT a
demo is the session: on a correct code we normalize the phone to E.164 and issue
a real HMAC-signed bearer token (see ``app.auth``). That token is what authorizes
every per-user action afterwards (contacts, SOS), so swapping in a real SMS OTP
later is localised to the ``send_otp`` function.
"""

from __future__ import annotations

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from ..auth import issue_token
from ..config import get_settings
from ..services.phone import PhoneValidationError, mask_phone, normalize_phone

router = APIRouter(prefix="/api/auth")


class SendOtpRequest(BaseModel):
    phone: str
    country: str | None = None


class VerifyOtpRequest(BaseModel):
    phone: str
    code: str
    country: str | None = None


@router.post("/send-otp")
async def send_otp(body: SendOtpRequest) -> dict:
    settings = get_settings()
    try:
        norm = normalize_phone(body.phone, default_region=body.country or settings.default_country)
    except PhoneValidationError as exc:
        return {"error": f"Enter a valid phone number: {exc}"}
    # Demo: pretend to send. The verify step accepts the fixed demo code.
    logger.info(f"[auth] (demo) OTP requested for {mask_phone(norm.e164)}")
    return {"success": True, "demo": True}


@router.post("/verify-otp")
async def verify_otp(body: VerifyOtpRequest) -> dict:
    settings = get_settings()
    try:
        norm = normalize_phone(body.phone, default_region=body.country or settings.default_country)
    except PhoneValidationError as exc:
        return {"error": f"Enter a valid phone number: {exc}"}

    if body.code.strip() != settings.demo_otp_code:
        return {"error": "Invalid or expired code"}

    # Identity = the E.164 phone. The token is the real credential from here on.
    token = issue_token(norm.e164)
    logger.info(f"[auth] session issued for {mask_phone(norm.e164)}")
    return {
        "success": True,
        "phone": norm.e164,
        "country": norm.country,
        "token": token,
        "tokenType": "Bearer",
    }
