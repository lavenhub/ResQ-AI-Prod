"""Provider selection from configuration.

``SMS_PROVIDER`` picks the implementation. "mock" (default) never contacts a real
carrier; "msg91" is the real India provider. The instance is cached per-process.
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

from ...config import get_settings
from .base import SmsProvider
from .email_provider import EmailSmsProvider
from .mock import MockSmsProvider
from .msg91 import Msg91SmsProvider


@lru_cache
def get_provider() -> SmsProvider:
    settings = get_settings()
    name = (settings.sms_provider or "mock").strip().lower()
    if name == "msg91":
        provider: SmsProvider = Msg91SmsProvider()
    elif name in ("email", "smtp"):
        provider = EmailSmsProvider()
    elif name == "mock":
        provider = MockSmsProvider(callback_secret=settings.sms_callback_secret or None)
    else:
        logger.warning(f"[sms] unknown SMS_PROVIDER '{name}', falling back to mock")
        provider = MockSmsProvider(callback_secret=settings.sms_callback_secret or None)
    problems = provider.validate_configuration()
    if problems:
        logger.warning(
            f"[sms] provider '{provider.name}' configuration incomplete: {'; '.join(problems)}"
        )
    else:
        logger.info(f"[sms] provider '{provider.name}' configured")
    return provider


def reset_provider_cache() -> None:
    """Testing helper — drop the cached provider so a new config takes effect."""
    get_provider.cache_clear()
