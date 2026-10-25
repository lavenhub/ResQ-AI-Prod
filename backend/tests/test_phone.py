"""Unit tests: phone-number normalization/validation (E.164)."""

from __future__ import annotations

import pytest

from app.services.phone import (
    PhoneValidationError,
    is_valid_phone,
    mask_phone,
    normalize_phone,
)


def test_normalizes_indian_national_to_e164():
    n = normalize_phone("98765 43210", default_region="IN")
    assert n.e164 == "+919876543210"
    assert n.country == "IN"
    assert n.digits == "919876543210"


def test_respects_explicit_country_code_over_default():
    # A US number must NOT be forced into India just because default is IN.
    n = normalize_phone("+1 415 555 2671", default_region="IN")
    assert n.e164 == "+14155552671"
    assert n.country == "US"


def test_rejects_garbage():
    with pytest.raises(PhoneValidationError):
        normalize_phone("not a phone", default_region="IN")


def test_rejects_impossible_number():
    with pytest.raises(PhoneValidationError):
        normalize_phone("+91 11111 11111", default_region="IN")


def test_is_valid_phone_boolean():
    assert is_valid_phone("+919876543210")
    assert not is_valid_phone("12")


def test_mask_phone_hides_the_middle():
    masked = mask_phone("+919876543210")
    assert masked.startswith("+91")
    assert masked.endswith("10")
    assert "9876" not in masked


def test_empty_is_error():
    with pytest.raises(PhoneValidationError):
        normalize_phone("", default_region="IN")
