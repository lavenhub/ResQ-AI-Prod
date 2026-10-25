"""Email provider abstraction + implementations.

Public surface:
    get_provider()  -> the configured EmailProvider (mock | resend)
    EmailProvider   -> the interface notification logic depends on
    OutboundEmail, SendResult, DeliveryUpdate, SendOutcome
"""

from .base import (
    DeliveryUpdate,
    EmailProvider,
    OutboundEmail,
    SendOutcome,
    SendResult,
)
from .factory import get_provider, reset_provider_cache

__all__ = [
    "DeliveryUpdate",
    "EmailProvider",
    "OutboundEmail",
    "SendOutcome",
    "SendResult",
    "get_provider",
    "reset_provider_cache",
]
