"""Phone-number normalization and validation (E.164).

Uses Google's `phonenumbers` library (the maintained port of libphonenumber) so we
never hand-roll country-code logic. Every number that enters the system — a user's
own phone at OTP time, and every emergency contact — is normalized to E.164 here,
with the country made explicit rather than silently assumed to be India.

E.164 (e.g. ``+919876543210``) is the one canonical form we store and compare, so
duplicate detection and provider formatting are unambiguous.
"""

from __future__ import annotations

from dataclasses import dataclass

import phonenumbers

# Default region used ONLY when a number has no leading '+'. Configurable by the
# caller; we never assume India for an already-international (+..) number.
DEFAULT_REGION = "IN"


class PhoneValidationError(ValueError):
    """Raised when a phone number cannot be parsed/validated to E.164."""


@dataclass(frozen=True)
class NormalizedPhone:
    e164: str          # canonical, e.g. "+919876543210"
    country: str       # ISO region, e.g. "IN"
    national: str      # national (dialled-locally) form, for display
    msg91_mobiles: str  # digits only incl. country code, e.g. "919876543210"


def normalize_phone(raw: str, *, default_region: str = DEFAULT_REGION) -> NormalizedPhone:
    """Parse ``raw`` to a validated E.164 number.

    ``default_region`` is applied ONLY to numbers written without a '+' country
    prefix. A number already in international form keeps its own country.

    Raises ``PhoneValidationError`` for anything not a valid, dialable number.
    """
    if raw is None or not str(raw).strip():
        raise PhoneValidationError("Phone number is empty")

    text = str(raw).strip()
    region = (default_region or DEFAULT_REGION).upper()

    try:
        parsed = phonenumbers.parse(text, None if text.startswith("+") else region)
    except phonenumbers.NumberParseException as exc:  # pragma: no cover - message varies
        raise PhoneValidationError(f"Could not parse phone number: {exc}") from exc

    if not phonenumbers.is_possible_number(parsed):
        raise PhoneValidationError("Phone number is not a possible number (wrong length?)")
    if not phonenumbers.is_valid_number(parsed):
        raise PhoneValidationError("Phone number is not valid for its country")

    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    national = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL)
    country = phonenumbers.region_code_for_number(parsed) or region
    return NormalizedPhone(
        e164=e164,
        country=country,
        national=national,
        # MSG91 wants digits only, including the country code, no '+'.
        msg91_mobiles=e164.lstrip("+"),
    )


def is_valid_phone(raw: str, *, default_region: str = DEFAULT_REGION) -> bool:
    try:
        normalize_phone(raw, default_region=default_region)
        return True
    except PhoneValidationError:
        return False


def mask_phone(e164: str) -> str:
    """Mask a number for logs/UI: keep country code + last 2 digits.

    e.g. ``+919876543210`` -> ``+91******10``. Keeps logs free of full PII.
    """
    if not e164:
        return ""
    digits = e164.lstrip("+")
    if len(digits) <= 4:
        return "*" * len(digits)
    prefix = e164[: len(e164) - len(digits) + 2]  # '+' + first 2 (country-ish)
    return f"{prefix}{'*' * (len(digits) - 4)}{digits[-2:]}"
