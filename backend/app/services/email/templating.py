"""Emergency + verification email rendering (subject + plain-text + HTML).

Content rules (mission Phase 6) enforced here:
  * clear, professional, mobile-friendly, accessible, concise, emergency-focused
  * EVERY user-provided value is HTML-escaped (no HTML/script injection)
  * HTML and plain-text parts are kept consistent
  * a full Google Maps link ONLY when real coordinates exist (never fabricated)
  * accurate timestamp with timezone + a short incident reference
  * NO medical dumps, transcripts, tokens, or sensitive data
  * it NEVER implies a vehicle/rescue has been dispatched
  * no tracking pixels, no attachments

Templates live here (reusable) rather than being hardcoded in the SOS controller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from urllib.parse import quote

# Brand colour (matches the ResQAI neo-brutalist red).
_RED = "#C62828"


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
    # Unambiguous UTC time with an explicit zone label.
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def _shell(inner_html: str, *, preheader: str) -> str:
    """Wrap body content in a responsive, theme-tolerant email shell.

    Inline styles only (email clients strip <style>/external CSS); a light,
    centered card with a max width that collapses cleanly on mobile.
    """
    safe_pre = escape(preheader)
    return (
        '<!doctype html><html lang="en"><head>'
        '<meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<title>Emergency alert</title></head>"
        '<body style="margin:0;padding:0;background:#f2f2f2;'
        '-webkit-text-size-adjust:100%;">'
        f'<div style="display:none;max-height:0;overflow:hidden;opacity:0;">{safe_pre}</div>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="background:#f2f2f2;padding:16px 0;"><tr><td align="center">'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="max-width:560px;width:100%;background:#ffffff;border:2px solid #000000;">'
        f"<tr><td>{inner_html}</td></tr>"
        "</table>"
        '<p style="max-width:560px;color:#888;font:400 11px/1.5 Arial,sans-serif;'
        'padding:12px 16px;margin:0;">You are receiving this because you were listed '
        "as an emergency contact in ResQAI. This is an automated safety notification.</p>"
        "</td></tr></table></body></html>"
    )


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
    note: str | None,
    incident_id: str,
    latitude: float | None,
    longitude: float | None,
    address: str | None = None,
    when: datetime | None = None,
) -> RenderedEmail:
    """Render the emergency alert email (subject + plain-text + responsive HTML)."""
    name = (victim_name or "Someone you know").strip() or "Someone you know"
    kind = (incident_type or "an emergency").strip() or "an emergency"
    ref = short_incident_ref(incident_id)
    time_str = _fmt_time(when)
    has_loc = latitude is not None and longitude is not None
    maps = google_maps_url(latitude, longitude) if has_loc else None
    addr = (address or "").strip()

    subject = f"{brand} EMERGENCY ALERT — {name} may need assistance"

    # ── plain-text part ───────────────────────────────────────────────────────
    lines = [
        f"{brand} EMERGENCY ALERT",
        "",
        f"An emergency alert has been triggered by {name}.",
        "",
        "Incident details:",
        f"- Incident reference: {ref}",
        f"- Alert time: {time_str}",
        f"- Emergency category: {kind}",
    ]
    if addr:
        lines.append(f"- Reported location: {addr}")
    if maps:
        lines.append(f"- Location on map: {maps}")
    else:
        lines.append("- Location: not available")
    if note and note.strip():
        lines += ["", f"Note from the alert: {note.strip()}"]
    lines += [
        "",
        "What you should do:",
        "1. Try contacting the person immediately.",
        "2. Check whether they need assistance.",
        "3. If the situation appears life-threatening, contact your local emergency "
        "services (in India: 112 / 108).",
        "4. Share the incident reference above when coordinating help.",
        "",
        f"IMPORTANT: This notification was generated by {brand}. It does NOT mean an "
        "ambulance, police unit, or rescue team has been dispatched.",
    ]
    text = "\n".join(lines)

    # ── HTML part (everything user-provided is escaped) ─────────────────────────
    e_name, e_kind, e_ref, e_time = escape(name), escape(kind), escape(ref), escape(time_str)
    loc_row = (
        f'<tr><td style="padding:2px 0;color:#555;">Location</td>'
        f'<td style="padding:2px 0;font-weight:bold;">'
        f'<a href="{maps}" style="color:{_RED};">View on map</a></td></tr>'
        if maps
        else '<tr><td style="padding:2px 0;color:#555;">Location</td>'
        '<td style="padding:2px 0;font-weight:bold;">Not available</td></tr>'
    )
    addr_row = (
        f'<tr><td style="padding:2px 0;color:#555;">Reported&nbsp;address</td>'
        f'<td style="padding:2px 0;font-weight:bold;">{escape(addr)}</td></tr>'
        if addr
        else ""
    )
    note_block = (
        f'<p style="margin:12px 0 0;padding:10px 12px;background:#fff8e1;'
        f'border:1px solid #f0c36d;font:400 13px/1.5 Arial,sans-serif;">'
        f"<strong>Note:</strong> {escape(note.strip())}</p>"
        if note and note.strip()
        else ""
    )
    map_button = (
        f'<p style="margin:16px 0;"><a href="{maps}" '
        f'style="display:inline-block;background:{_RED};color:#ffffff;text-decoration:none;'
        f'font:bold 14px Arial,sans-serif;padding:12px 20px;border:2px solid #000000;">'
        "VIEW LOCATION ON MAP</a></p>"
        if maps
        else ""
    )

    inner = (
        f'<div style="background:{_RED};padding:16px 20px;">'
        f'<span style="color:#ffffff;font:bold 18px Arial,sans-serif;letter-spacing:1px;">'
        f"{escape(brand)} EMERGENCY ALERT</span></div>"
        '<div style="padding:20px;font:400 14px/1.6 Arial,sans-serif;color:#111;">'
        f"<p style=\"margin:0 0 12px;font-size:16px;\">An emergency alert has been "
        f"triggered by <strong>{e_name}</strong>.</p>"
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="font:400 13px/1.5 Arial,sans-serif;border-top:1px solid #eee;'
        'border-bottom:1px solid #eee;padding:8px 0;margin:8px 0;">'
        f'<tr><td style="padding:2px 0;color:#555;width:40%;">Incident&nbsp;reference</td>'
        f'<td style="padding:2px 0;font-weight:bold;">{e_ref}</td></tr>'
        f'<tr><td style="padding:2px 0;color:#555;">Alert&nbsp;time</td>'
        f'<td style="padding:2px 0;font-weight:bold;">{e_time}</td></tr>'
        f'<tr><td style="padding:2px 0;color:#555;">Emergency&nbsp;category</td>'
        f'<td style="padding:2px 0;font-weight:bold;">{e_kind}</td></tr>'
        f"{addr_row}{loc_row}"
        "</table>"
        f"{map_button}{note_block}"
        '<p style="margin:16px 0 6px;font-weight:bold;">What you should do:</p>'
        '<ol style="margin:0;padding-left:20px;">'
        "<li>Try contacting the person immediately.</li>"
        "<li>Check whether they need assistance.</li>"
        "<li>If the situation appears life-threatening, contact your local emergency "
        "services (in India: <strong>112 / 108</strong>).</li>"
        "<li>Share the incident reference above when coordinating help.</li>"
        "</ol>"
        f'<p style="margin:16px 0 0;padding:10px 12px;background:#f5f5f5;'
        f'border:1px solid #ddd;color:#555;font-size:12px;">This notification was '
        f"generated by {escape(brand)}. It does <strong>not</strong> mean an ambulance, "
        "police unit, or rescue team has been dispatched.</p>"
        "</div>"
    )
    html = _shell(inner, preheader=f"{name} may need assistance — incident {ref}")
    return RenderedEmail(subject=subject, text=text, html=html, location_included=has_loc)


_BOOKING_KIND_INTRO = {
    "initiated": "An ambulance-coordination call has been started.",
    "connected": "The ambulance reception desk answered the coordination call.",
    "confirmed": "The ambulance reception confirmed the booking.",
    "declined": "The ambulance reception could not take the booking.",
    "failed": "The ambulance-coordination call could not be completed.",
}
_BOOKING_KIND_SUBJECT = {
    "initiated": "Ambulance coordination started",
    "connected": "Reception contacted",
    "confirmed": "Ambulance booking CONFIRMED",
    "declined": "Ambulance booking declined",
    "failed": "Ambulance call failed",
}


def render_booking_update_email(
    *,
    brand: str,
    kind: str,
    service_name: str | None,
    is_demo: bool,
    booking_state: str,
    availability: str,
    eta: str | None,
    charges: str | None,
    booking_reference: str | None,
    patient_condition: str | None,
    pickup_address: str | None,
    incident_id: str,
    when: datetime | None = None,
) -> RenderedEmail:
    """Render an authorized ambulance-booking status update.

    Honesty rules (mission Phases 7 & 9): states only what the backend recorded;
    ETA / charges / reference appear ONLY when actually provided; NEVER claims a
    dispatch. A demo call is labelled as such.
    """
    ref = short_incident_ref(incident_id)
    time_str = _fmt_time(when)
    intro = _BOOKING_KIND_INTRO.get(kind, "Ambulance-coordination status update.")
    subj_tail = _BOOKING_KIND_SUBJECT.get(kind, "Ambulance coordination update")
    demo_tag = " (DEMONSTRATION)" if is_demo else ""
    subject = f"{brand}{demo_tag}: {subj_tail} — ref {ref}"

    # Only include facts that actually exist.
    facts: list[tuple[str, str]] = [
        ("Incident reference", ref),
        ("Update time", time_str),
        ("Status", booking_state.replace("_", " ").title()),
    ]
    if service_name:
        facts.append(("Service", service_name + (" — DEMO reception number" if is_demo else "")))
    if availability and availability != "UNKNOWN":
        facts.append(("Ambulance availability", availability.title()))
    if eta:
        facts.append(("Estimated arrival", eta))
    if charges:
        facts.append(("Charges", charges))
    if booking_reference:
        facts.append(("Booking reference", booking_reference))
    if patient_condition:
        facts.append(("Patient condition", patient_condition))
    if pickup_address:
        facts.append(("Pickup", pickup_address))

    # ── plain text ──────────────────────────────────────────────────────────
    lines = [f"{brand} ambulance coordination update{demo_tag}", "", intro, ""]
    lines += [f"- {k}: {v}" for k, v in facts]
    lines += [
        "",
        f"IMPORTANT: This update reflects only what {brand} has recorded from the "
        "coordination call. It does NOT confirm an ambulance has physically been "
        "dispatched or arrived. For a life-threatening emergency, call 112 / 108.",
    ]
    text = "\n".join(lines)

    # ── HTML ────────────────────────────────────────────────────────────────
    rows = "".join(
        f'<tr><td style="padding:2px 0;color:#555;width:42%;">{escape(k)}</td>'
        f'<td style="padding:2px 0;font-weight:bold;">{escape(v)}</td></tr>'
        for k, v in facts
    )
    inner = (
        f'<div style="background:{_RED};padding:16px 20px;">'
        f'<span style="color:#ffffff;font:bold 18px Arial,sans-serif;letter-spacing:1px;">'
        f"{escape(brand)} AMBULANCE UPDATE{escape(demo_tag)}</span></div>"
        '<div style="padding:20px;font:400 14px/1.6 Arial,sans-serif;color:#111;">'
        f'<p style="margin:0 0 12px;font-size:16px;">{escape(intro)}</p>'
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="font:400 13px/1.5 Arial,sans-serif;border-top:1px solid #eee;'
        'border-bottom:1px solid #eee;padding:8px 0;margin:8px 0;">'
        f"{rows}</table>"
        f'<p style="margin:16px 0 0;padding:10px 12px;background:#f5f5f5;'
        f'border:1px solid #ddd;color:#555;font-size:12px;">This update reflects only '
        f"what {escape(brand)} recorded from the coordination call. It does "
        "<strong>not</strong> confirm an ambulance has been dispatched or arrived. "
        "For a life-threatening emergency, call <strong>112 / 108</strong>.</p>"
        "</div>"
    )
    html = _shell(inner, preheader=f"{subj_tail} — incident {ref}")
    return RenderedEmail(subject=subject, text=text, html=html, location_included=bool(pickup_address))


def render_verification_email(
    *,
    brand: str,
    contact_name: str | None,
    owner_label: str | None,
    verify_url: str,
) -> RenderedEmail:
    """Render the contact email-verification email.

    ``verify_url`` is a backend-built single-use link; it is a URL we generated
    (not user input) so it is placed in href as-is. All free-text is escaped.
    """
    name = (contact_name or "there").strip() or "there"
    who = (owner_label or "a ResQAI user").strip() or "a ResQAI user"

    subject = f"Confirm you are an emergency contact on {brand}"
    text = "\n".join(
        [
            f"Hi {name},",
            "",
            f"{who} added you as an emergency contact on {brand}.",
            "If you agree to receive emergency alerts for them, confirm your email:",
            "",
            verify_url,
            "",
            "If you did not expect this, you can ignore this email — no alerts will be "
            "sent to you unless you confirm. This link expires soon and can be used once.",
            "",
            f"— {brand}",
        ]
    )
    e_name, e_who, e_brand = escape(name), escape(who), escape(brand)
    inner = (
        f'<div style="background:{_RED};padding:16px 20px;">'
        f'<span style="color:#ffffff;font:bold 18px Arial,sans-serif;letter-spacing:1px;">'
        f"{e_brand}</span></div>"
        '<div style="padding:20px;font:400 14px/1.6 Arial,sans-serif;color:#111;">'
        f'<p style="margin:0 0 12px;">Hi {e_name},</p>'
        f"<p style=\"margin:0 0 12px;\"><strong>{e_who}</strong> added you as an "
        f"emergency contact on {e_brand}. If you agree to receive emergency alerts "
        "for them, please confirm your email address.</p>"
        f'<p style="margin:16px 0;"><a href="{verify_url}" '
        f'style="display:inline-block;background:{_RED};color:#ffffff;text-decoration:none;'
        f'font:bold 14px Arial,sans-serif;padding:12px 20px;border:2px solid #000000;">'
        "CONFIRM MY EMAIL</a></p>"
        '<p style="margin:12px 0;color:#555;font-size:12px;">If you did not expect this, '
        "you can ignore this email — you will not receive any alerts unless you confirm. "
        "This link expires soon and can be used only once.</p>"
        "</div>"
    )
    html = _shell(inner, preheader=f"Confirm you are an emergency contact on {brand}")
    return RenderedEmail(subject=subject, text=text, html=html, location_included=False)
