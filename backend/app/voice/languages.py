"""Map the frontend's language names to Pipecat `Language` enum members.

The frontend offers 22 Indian languages. Gemini Live natively handles English,
Hindi and Marathi (our Phase 6 targets, both confirmed: hi-IN, mr-IN) plus many
others. Anything we can't map to a supported India-variant falls back to Hindi
with `native=False`, so callers can be honest that the requested language is not
natively supported yet.
"""

from __future__ import annotations

from pipecat.transcriptions.language import Language

# Only members we are reasonably confident Gemini Live supports as audio.
_NAME_TO_ENUM: dict[str, str] = {
    "English": "EN_IN",
    "Hindi": "HI_IN",
    "Marathi": "MR_IN",
    "Bengali": "BN_IN",
    "Telugu": "TE_IN",
    "Tamil": "TA_IN",
    "Gujarati": "GU_IN",
    "Kannada": "KN_IN",
    "Malayalam": "ML_IN",
    "Punjabi": "PA_IN",
    "Odia": "OR_IN",
    "Nepali": "NE_NP",
    "Urdu": "UR_IN",
}

# Our three fully-targeted + tested-intent languages for this build.
PRIMARY_LANGUAGES = frozenset({"English", "Hindi", "Marathi"})

_FALLBACK = Language.HI_IN


def _lookup(member_name: str) -> Language | None:
    return getattr(Language, member_name, None)


def resolve_language(name: str | None) -> tuple[Language, bool]:
    """Return (Language enum, natively_supported).

    `natively_supported` is False when we had to fall back, so the caller can
    disclose the limitation instead of pretending full support.
    """
    if not name:
        return Language.EN_IN, True
    member = _NAME_TO_ENUM.get(name.strip())
    if member:
        enum = _lookup(member)
        if enum is not None:
            return enum, True
    return _FALLBACK, False


def bcp47(name: str | None) -> str:
    """Human-readable BCP-47 code for storing on the incident record."""
    lang, _ = resolve_language(name)
    return lang.value
