"""Build the emergency SMS body + the provider template-variable map.

Content rules (mission Phase 6) enforced here:
  * concise, readable, actionable; tells the recipient what to DO
  * full, URL-encoded Google Maps link ONLY when real coordinates exist
    (never fabricated); no API key needed for a maps search URL
  * accurate timestamp + short incident reference
  * no medical dumps, transcripts, tokens, or sensitive AI history
  * does NOT imply a vehicle/rescue has been dispatched
  * length-aware (we keep it within a small number of GSM segments)

The *rendered* body is what the mock provider and our audit store. For a real
DLT template provider (MSG91) the carrier substitutes variables into the
pre-approved template; we therefore also return a ``variables`` map whose keys
match the template, and we keep the local body and the template wording aligned.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote


def google_maps_url(lat: float, lng: float) -> str:
    """Standard Maps search URL for a coordinate (no API key required).

    Uses the documented ``?api=1&query=lat,lng`` form, URL-encoded.
    """
    query = quote(f"{lat:.6f},{lng:.6f}", safe="")
    return f"https://www.google.com/maps/search/?api=1&query={query}"


def short_incident_ref(incident_id: str) -> str:
    """A short, human-quotable reference derived from the incident id."""
    return incident_id.replace("-", "")[:8].upper()


def _fmt_time(ts: datetime | None) -> str:
    dt = ts or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Short, unambiguous UTC time. (Localization to the recipient is a future task.)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


@dataclass(frozen=True)
class RenderedMessage:
    body: str
    variables: dict[str, str]
    location_included: bool


def render_emergency_sms(
    *,
    brand: str,
    victim_name: str | None,
    incident_type: str | None,
    incident_id: str,
    latitude: float | None,
    longitude: float | None,
    when: datetime | None = None,
) -> RenderedMessage:
    """Render the alert body and the matching template variable map."""
    name = (victim_name or "Someone you know").strip() or "Someone you know"
    kind = (incident_type or "an emergency").strip() or "an emergency"
    ref = short_incident_ref(incident_id)
    time_str = _fmt_time(when)

    has_loc = latitude is not None and longitude is not None
    loc_clause = (
        f"Location: {google_maps_url(latitude, longitude)}. "
        if has_loc
        else "Location not available. "
    )

    # Keep this wording aligned with the DLT-approved template in the provider panel.
    body = (
        f"{brand} EMERGENCY: {name} may need urgent help ({kind}). "
        f"Time: {time_str}. "
        f"{loc_clause}"
        f"Please try to reach them and call local emergency services if needed. "
        f"Ref: {ref}. "
        f"(Automated alert; no rescue has been dispatched.)"
    )

    variables = {
        "brand": brand,
        "name": name,
        "incident": kind,
        "time": time_str,
        "location": google_maps_url(latitude, longitude) if has_loc else "not available",
        "ref": ref,
    }
    return RenderedMessage(body=body, variables=variables, location_included=has_loc)


@dataclass(frozen=True)
class RenderedEmail:
    subject: str
    text: str
    html: str
    location_included: bool


def render_emergency_email(
    *,
    brand: str,
    victim_name: str | None,
    incident_type: str | None,
    incident_id: str,
    latitude: float | None,
    longitude: float | None,
    when: datetime | None = None,
) -> RenderedEmail:
    """Render the emergency email (subject + plain-text + minimal HTML).

    Reuses the same honest content rules as the SMS body; the only additions are
    a subject line and a tappable Maps link in the HTML part. No tracking pixels,
    no sensitive data, and it never implies a rescue was dispatched.
    """
    msg = render_emergency_sms(
        brand=brand,
        victim_name=victim_name,
        incident_type=incident_type,
        incident_id=incident_id,
        latitude=latitude,
        longitude=longitude,
        when=when,
    )
    name = msg.variables["name"]
    kind = msg.variables["incident"]
    subject = f"{brand} EMERGENCY — {name} may need urgent help ({kind})"

    has_loc = msg.location_included
    maps = msg.variables["location"]
    loc_html = (
        f'<p><strong>Location:</strong> <a href="{maps}">{maps}</a></p>'
        if has_loc
        else "<p><strong>Location:</strong> not available</p>"
    )
    html = (
        f"<div style=\"font-family:system-ui,Arial,sans-serif;line-height:1.5\">"
        f"<h2 style=\"color:#C62828;margin:0 0 8px\">{brand} EMERGENCY</h2>"
        f"<p><strong>{name}</strong> may need urgent help (<strong>{kind}</strong>).</p>"
        f"<p><strong>Time:</strong> {msg.variables['time']}</p>"
        f"{loc_html}"
        f"<p>Please try to reach them and call local emergency services if needed.</p>"
        f"<p style=\"color:#666;font-size:12px\">Incident ref: {msg.variables['ref']}. "
        f"Automated alert from {brand}; no rescue has been dispatched.</p>"
        f"</div>"
    )
    return RenderedEmail(subject=subject, text=msg.body, html=html, location_included=has_loc)


def segment_count(body: str) -> int:
    """Approximate SMS segment count.

    GSM-7 single segment = 160 chars; concatenated = 153/segment. If the body
    contains non-GSM characters we fall back to UCS-2 limits (70 / 67). Our Maps
    URLs and ASCII body stay in GSM-7, but we check so callers can warn.
    """
    gsm_basic = (
        "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞ ÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
        "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
    )
    gsm_ext = "^{}\\[~]|€"
    is_gsm = all(c in gsm_basic or c in gsm_ext for c in body)
    length = len(body) + sum(1 for c in body if c in gsm_ext)
    if is_gsm:
        return 1 if length <= 160 else -(-length // 153)
    ucs_len = len(body)
    return 1 if ucs_len <= 70 else -(-ucs_len // 67)
