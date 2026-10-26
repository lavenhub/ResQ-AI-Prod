"""Telephony provider abstraction (outbound PSTN calls).

Public surface mirrors services/email: import the interface + value types and the
``get_telephony_provider`` factory from here; never import a concrete provider
module directly from business logic.
"""

from __future__ import annotations

from .base import (
    AnswerDocument,
    CallRequest,
    CallResult,
    CallStatusUpdate,
    TelephonyProvider,
)
from .factory import get_telephony_provider, reset_telephony_cache

__all__ = [
    "AnswerDocument",
    "CallRequest",
    "CallResult",
    "CallStatusUpdate",
    "TelephonyProvider",
    "get_telephony_provider",
    "reset_telephony_cache",
]
