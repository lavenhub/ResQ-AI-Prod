"""Authoritative emergency-service reference data.

This is STABLE REFERENCE DATA — official, government-published helpline numbers —
NOT a live feed. The numbers here change rarely; when they are served to the LLM
they are labelled as "official reference", distinct from live-retrieved facts
(see `retrieval.py`) and distinct from unverified caller claims.

India is the primary jurisdiction (ResQAI is built for India). The unified
emergency number 112 (ERSS) reaches police, fire and ambulance nationwide, so it
is always offered as the safe default. Category-specific helplines are provided
in addition, never instead of, 112/108.

Sources: Government of India Emergency Response Support System (112), National
Health Mission (108/102/104), NDMA (1070/1078), MoWCD (1098/181), MoSJE Elderline
(14567), MoHFW Tele-MANAS (14416). Verify against the official directory
periodically: https://www.112.gov.in/ and https://ndma.gov.in/
"""

from __future__ import annotations

from dataclasses import dataclass

# Categories the agent may ask for. Deliberately coarse — they map scenario
# families to the most appropriate helpline(s). Keep in sync with
# `voice/scenarios.py` category names where they overlap.
CATEGORIES = (
    "general",        # anything / unsure -> 112
    "medical",        # illness, injury, cardiac, breathing, etc.
    "fire",           # fire, smoke, explosion
    "police",         # crime, violence, threat
    "accident",       # road / highway accident
    "pregnancy",      # maternal / childbirth
    "child",          # child in distress / abuse
    "women",          # women's safety / domestic abuse
    "disaster",       # flood, earthquake, cyclone, landslide
    "mental_health",  # self-harm, suicidal thoughts, acute distress
    "senior",         # older-adult specific
    "poison",         # poisoning / overdose
    "cyber",          # cyber fraud / online threat
)


@dataclass(frozen=True)
class Helpline:
    number: str
    name: str
    note: str = ""


# ── India (default jurisdiction) ──────────────────────────────────────────────
# `_IN_UNIVERSAL` is always surfaced first; `_IN_BY_CATEGORY` adds specifics.
_IN_UNIVERSAL: tuple[Helpline, ...] = (
    Helpline("112", "All-in-one emergency (ERSS)",
             "Reaches police, fire and ambulance anywhere in India. Works even "
             "with no SIM / locked phone. Use this if unsure."),
)

_IN_BY_CATEGORY: dict[str, tuple[Helpline, ...]] = {
    "general": (),
    "medical": (
        Helpline("108", "Ambulance / medical emergency"),
        Helpline("102", "Pregnancy & infant ambulance (free, most states)"),
        Helpline("104", "Health advice helpline", "For non-emergency medical questions."),
    ),
    "fire": (
        Helpline("101", "Fire & rescue"),
    ),
    "police": (
        Helpline("100", "Police"),
    ),
    "accident": (
        Helpline("108", "Ambulance / medical emergency"),
        Helpline("1073", "Road accident & national highway emergency"),
    ),
    "pregnancy": (
        Helpline("102", "Pregnancy & infant ambulance (free, most states)"),
        Helpline("108", "Ambulance / medical emergency"),
    ),
    "child": (
        Helpline("1098", "Childline — child in distress"),
    ),
    "women": (
        Helpline("1091", "Women helpline (emergency)"),
        Helpline("181", "Women helpline (domestic abuse / distress)"),
    ),
    "disaster": (
        Helpline("1070", "State disaster management control room"),
        Helpline("1077", "District disaster control room"),
        Helpline("108", "Ambulance / medical emergency"),
    ),
    "mental_health": (
        Helpline("14416", "Tele-MANAS national mental-health support (24x7)",
                 "Also 1-800-891-4416. Free, confidential."),
        Helpline("9152987821", "iCALL psychosocial helpline"),
    ),
    "senior": (
        Helpline("14567", "Elderline — senior citizen helpline"),
        Helpline("108", "Ambulance / medical emergency"),
    ),
    "poison": (
        Helpline("108", "Ambulance / medical emergency"),
        Helpline("1066", "National Poisons Information Centre (AIIMS)",
                 "Availability varies; if no answer call 112/108."),
    ),
    "cyber": (
        Helpline("1930", "Cyber-crime & online financial fraud"),
    ),
}

# Minimal honest fallback for jurisdictions we don't have a vetted directory for.
_GENERIC_UNIVERSAL: tuple[Helpline, ...] = (
    Helpline("112", "International emergency number",
             "Works in the EU, India and many GSM networks. If it does not "
             "connect, use the local emergency number for the caller's country."),
)


def normalise_category(category: str | None) -> str:
    if not category:
        return "general"
    c = category.strip().lower().replace("-", "_").replace(" ", "_")
    return c if c in CATEGORIES else "general"


def emergency_numbers_for(
    category: str | None = None, *, country: str = "IN"
) -> dict:
    """Return official reference helplines for a category + jurisdiction.

    The result is explicitly tagged `kind="official_reference"` so the agent (and
    the frontend) never confuse stable published numbers with live-retrieved or
    caller-provided facts.
    """
    cat = normalise_category(category)
    cc = (country or "IN").strip().upper()

    if cc == "IN":
        universal = _IN_UNIVERSAL
        specific = _IN_BY_CATEGORY.get(cat, ())
        jurisdiction = "India"
    else:
        universal = _GENERIC_UNIVERSAL
        specific = ()
        jurisdiction = cc

    def _fmt(h: Helpline) -> dict:
        d = {"number": h.number, "name": h.name}
        if h.note:
            d["note"] = h.note
        return d

    # De-dupe while preserving order (a specific list may repeat 108).
    seen: set[str] = set()
    numbers: list[dict] = []
    for h in (*universal, *specific):
        if h.number in seen:
            continue
        seen.add(h.number)
        numbers.append(_fmt(h))

    partial = cc != "IN"
    return {
        "kind": "official_reference",
        "jurisdiction": jurisdiction,
        "category": cat,
        "primary": universal[0].number if universal else None,
        "numbers": numbers,
        "partial": partial,
        "note": (
            "Official published helpline numbers (stable reference, not a live "
            "status check). Dialling a number does not guarantee immediate "
            "pickup; if one does not answer, use the primary number."
            + ("" if not partial else
               " Limited directory for this country — primary number only.")
        ),
    }
