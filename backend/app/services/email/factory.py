"""Provider selection from configuration.

``EMAIL_PROVIDER`` picks the implementation. "mock" (default) never sends a real
email; "resend" is the real transactional provider. The instance is cached
per-process.
"""

from __future__ import annotations

from functools import lru_cache

from loguru import logger

from ...config import get_settings
from .base import EmailProvider
from .mock import _new_mock
from .resend_provider import ResendEmailProvider


@lru_cache
def get_provider() -> EmailProvider:
    settings = get_settings()
    name = (settings.email_provider or "mock").strip().lower()
    if name == "resend":
        provider: EmailProvider = ResendEmailProvider()
    elif name == "mock":
        provider = _new_mock()
    else:
        logger.warning(f"[email] unknown EMAIL_PROVIDER '{name}', falling back to mock")
        provider = _new_mock()

    problems = provider.validate_configuration()
    if problems:
        logger.warning(
            f"[email] provider '{provider.name}' configuration incomplete: {'; '.join(problems)}"
        )
    else:
        logger.info(f"[email] provider '{provider.name}' configured")
    return provider


def reset_provider_cache() -> None:
    """Testing helper — drop the cached provider so a new config takes effect."""
    get_provider.cache_clear()
