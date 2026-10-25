"""Unit tests for email rendering: escaping, maps link, honesty, consistency."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.email.templating import (
    google_maps_url,
    render_emergency_email,
    render_verification_email,
    short_incident_ref,
)


def test_google_maps_url_format():
    url = google_maps_url(19.07, 72.87)
    assert url == "https://www.google.com/maps/search/?api=1&query=19.070000%2C72.870000"


def test_short_incident_ref():
    assert short_incident_ref("abcdef12-3456-7890") == "ABCDEF12"


def test_emergency_email_with_location():
    r = render_emergency_email(
        brand="ResQAI",
        victim_name="Asha",
        incident_type="Road Accident",
        note="near the bridge",
        incident_id="abcdef1234567890",
        latitude=19.07,
        longitude=72.87,
        address="Bandra",
        when=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
    )
    assert r.location_included is True
    # Subject names the person + follows the mission shape.
    assert "ResQAI EMERGENCY ALERT" in r.subject and "Asha" in r.subject
    # Maps link present in both parts.
    assert "google.com/maps" in r.text
    assert "google.com/maps" in r.html
    # Timestamp with zone + incident reference present.
    assert "2026-01-02 03:04 UTC" in r.text
    assert "ABCDEF12" in r.text and "ABCDEF12" in r.html
    # Honesty: never implies dispatch.
    assert "not" in r.text.lower() and "dispatch" in r.text.lower()
    assert "dispatch" in r.html.lower()
    # Actionable guidance + local emergency number.
    assert "112" in r.text and "112" in r.html


def test_emergency_email_without_location():
    r = render_emergency_email(
        brand="ResQAI",
        victim_name=None,
        incident_type=None,
        note=None,
        incident_id="id123456789",
        latitude=None,
        longitude=None,
    )
    assert r.location_included is False
    assert "google.com/maps" not in r.text
    assert "google.com/maps" not in r.html
    assert "not available" in r.text.lower()


def test_emergency_email_escapes_user_values():
    r = render_emergency_email(
        brand="ResQAI",
        victim_name="<script>alert(1)</script>",
        incident_type="<img src=x onerror=1>",
        note="<b>hi</b>",
        incident_id="id123456789",
        latitude=None,
        longitude=None,
    )
    # No raw user HTML survives into the HTML part.
    assert "<script>" not in r.html
    assert "<img" not in r.html
    assert "&lt;script&gt;" in r.html


def test_verification_email_contains_link_and_escapes():
    r = render_verification_email(
        brand="ResQAI",
        contact_name="<b>Mum</b>",
        owner_label="The ResQAI user +91******10",
        verify_url="https://app.example/api/contacts/verify?token=abc",
    )
    assert "verify?token=abc" in r.text
    assert "verify?token=abc" in r.html
    assert "<b>Mum</b>" not in r.html  # escaped
    assert "confirm" in r.text.lower()
