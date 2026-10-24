"""Unit tests: emergency SMS templating, Maps URL, segmentation."""

from __future__ import annotations

from datetime import datetime, timezone

from app.services.sms.templating import (
    google_maps_url,
    render_emergency_sms,
    segment_count,
    short_incident_ref,
)


def test_maps_url_is_encoded_and_keyless():
    url = google_maps_url(19.076, 72.8777)
    assert url.startswith("https://www.google.com/maps/search/?api=1&query=")
    assert "%2C" in url  # the comma between lat,lng is URL-encoded
    assert "key=" not in url  # a maps search URL needs no API key


def test_render_includes_location_when_coords_present():
    r = render_emergency_sms(
        brand="RESQAI",
        victim_name="Asha",
        incident_type="Road Accident",
        incident_id="abc123def456",
        latitude=19.076,
        longitude=72.8777,
        when=datetime(2026, 1, 2, 3, 4, tzinfo=timezone.utc),
    )
    assert r.location_included is True
    assert "Asha" in r.body
    assert "Road Accident" in r.body
    assert "google.com/maps" in r.body
    assert "2026-01-02 03:04 UTC" in r.body
    # Must not imply a rescue was dispatched.
    assert "dispatched" in r.body.lower()  # "...no rescue has been dispatched."
    assert "no rescue has been dispatched" in r.body.lower()
    assert r.variables["ref"] == short_incident_ref("abc123def456")


def test_render_without_location_is_still_useful():
    r = render_emergency_sms(
        brand="RESQAI",
        victim_name=None,
        incident_type=None,
        incident_id="zzz",
        latitude=None,
        longitude=None,
    )
    assert r.location_included is False
    assert "not available" in r.body.lower()
    assert "google.com/maps" not in r.body


def test_short_ref_is_stable_and_short():
    assert short_incident_ref("abcdefgh12345678") == "ABCDEFGH"


def test_segment_count_ascii_short_is_one():
    assert segment_count("hello") == 1


def test_segment_count_long_ascii_multisegment():
    assert segment_count("x" * 500) >= 3
