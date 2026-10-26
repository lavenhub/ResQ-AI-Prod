"""Telephony provider selection from configuration.

``TELEPHONY_PROVIDER`` picks the implementation. "mock" (default) never places a
real call; "twilio" is the verified real path; "exotel" is a shipped-but-
demo-unverified adapter. The instance is cached per-process.
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

from ...config import get_settings
from .base import TelephonyProvider
from .exotel_provider import ExotelTelephonyProvider
from .mock import _new_mock
from .twilio_provider import TwilioTelephonyProvider
from .webrtc_provider import WebRTCTelephonyProvider


@lru_cache
def get_telephony_provider() -> TelephonyProvider:
    settings = get_settings()
    name = (settings.telephony_provider or "mock").strip().lower()
    if name == "twilio":
        provider: TelephonyProvider = TwilioTelephonyProvider()
    elif name == "exotel":
        provider = ExotelTelephonyProvider()
    elif name == "webrtc":
        provider = WebRTCTelephonyProvider()
    elif name == "mock":
        provider = _new_mock()
    else:
        logger.warning(f"[telephony] unknown TELEPHONY_PROVIDER '{name}', falling back to mock")
        provider = _new_mock()

    problems = provider.validate_configuration()
    if provider.is_mock:
        logger.warning(
            "Telephony provider = MOCK — NO real phone call will be placed. Set "
            "TELEPHONY_PROVIDER=twilio (+ TELEPHONY_ACCOUNT_SID, TELEPHONY_AUTH_TOKEN, "
            "TELEPHONY_CALLER_ID, TELEPHONY_BASE_URL) for real outbound calls."
        )
    elif problems:
        logger.warning(
            f"[telephony] provider '{provider.name}' NOT ready: {'; '.join(problems)}"
        )
    else:
        logger.info(f"[telephony] provider '{provider.name}' ready for real calls")
    return provider


def reset_telephony_cache() -> None:
    """Testing helper — drop the cached provider so a new config takes effect."""
    get_telephony_provider.cache_clear()
