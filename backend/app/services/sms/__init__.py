"""SMS provider abstraction + implementations.

Public surface:
    get_provider()          -> the configured SmsProvider (mock | msg91)
    SmsProvider             -> the interface incident logic depends on
    OutboundSms, SendResult, DeliveryUpdate, SendOutcome
"""

from .base import (
    DeliveryUpdate,
    OutboundSms,
    SendOutcome,
    SendResult,
    SmsProvider,
)
from .factory import get_provider

__all__ = [
    "DeliveryUpdate",
    "OutboundSms",
    "SendOutcome",
    "SendResult",
    "SmsProvider",
    "get_provider",
]
