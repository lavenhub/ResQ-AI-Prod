"""Tests for the authoritative emergency-number reference data.

These numbers are safety-critical, so we pin the important ones and the
jurisdiction/category behaviour. No network — this is static reference data.
"""

from __future__ import annotations

import pytest

from app.services.emergency_reference import (
    CATEGORIES,
    emergency_numbers_for,
    normalise_category,
)


def test_india_general_offers_112_first():
    info = emergency_numbers_for("general", country="IN")
    assert info["jurisdiction"] == "India"
    assert info["primary"] == "112"
    assert info["kind"] == "official_reference"
    numbers = [n["number"] for n in info["numbers"]]
    assert numbers[0] == "112"


@pytest.mark.parametrize(
    "category,expected",
    [
        ("medical", "108"),
        ("fire", "101"),
        ("police", "100"),
        ("child", "1098"),
        ("women", "1091"),
        ("accident", "1073"),
        ("pregnancy", "102"),
        ("mental_health", "14416"),
        ("senior", "14567"),
        ("cyber", "1930"),
        ("disaster", "1070"),
    ],
)
def test_category_contains_expected_official_number(category, expected):
    info = emergency_numbers_for(category, country="IN")
    numbers = [n["number"] for n in info["numbers"]]
    # 112 is always present as the universal fallback...
    assert "112" in numbers
    # ...and the category-specific official number is present too.
    assert expected in numbers


def test_unknown_category_falls_back_to_general():
    info = emergency_numbers_for("teleportation-accident", country="IN")
    assert info["category"] == "general"
    assert info["primary"] == "112"


def test_none_category_is_general():
    assert emergency_numbers_for(None)["category"] == "general"


def test_numbers_are_deduped_and_well_formed():
    info = emergency_numbers_for("accident", country="IN")
    numbers = [n["number"] for n in info["numbers"]]
    assert len(numbers) == len(set(numbers))  # no duplicates (108 could repeat)
    for n in info["numbers"]:
        assert n["number"] and n["name"]


def test_non_india_jurisdiction_is_partial_and_honest():
    info = emergency_numbers_for("medical", country="US")
    assert info["partial"] is True
    assert info["jurisdiction"] == "US"
    # We don't pretend to know US category helplines; only the universal number.
    assert info["primary"] == "112"
    assert "partial" in info["note"].lower() or "limited" in info["note"].lower()


def test_note_does_not_overclaim_live_status():
    # Reference data must not imply it checked that a line is answering now.
    note = emergency_numbers_for("medical")["note"].lower()
    assert "not a live" in note


def test_normalise_category_handles_separators():
    assert normalise_category("mental-health") == "mental_health"
    assert normalise_category("Mental Health") == "mental_health"
    assert normalise_category("  FIRE ") == "fire"
    assert normalise_category("bogus") == "general"


def test_all_categories_resolve():
    for cat in CATEGORIES:
        info = emergency_numbers_for(cat, country="IN")
        assert info["numbers"], f"{cat} produced no numbers"
