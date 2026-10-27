"""The ambulance-coordination AI for the LOCAL simulated reception call.

A human (the presenter, in front of the judges) plays the hospital receptionist
and talks to this AI. The AI leads the call: it discloses it is an automated
ResQAI assistant, explains the emergency using ONLY the verified booking facts
(patient condition, emergency type, pickup address + coordinates), asks about
availability / ambulance type / ETA / charges, and requests confirmation.

All over plain HTTPS Gemini calls (app.agent.gemini_generate) — no telephony, no
WebRTC, nothing hosted. The structured outcome (availability/ETA/charges/ref/
confirmed) is extracted from the finished transcript with one JSON call.
"""

from __future__ import annotations

import json

from loguru import logger

from .agent import AgentError, gemini_generate

_COORDINATOR_SYSTEM = """\
You are ResQ, an automated AI assistant calling a hospital/ambulance reception
desk on behalf of ResQAI to arrange an emergency ambulance. You are speaking with
the RECEPTIONIST (a real person). Lead the call politely and efficiently.

# RULES
- In your FIRST line, clearly disclose you are an automated AI assistant calling
  from ResQAI about an emergency ambulance request. Do not pretend to be human.
- Explain the emergency using ONLY the verified details below. NEVER invent a
  name, condition, address, or coordinates.
- Share the patient's condition and the pickup location + coordinates clearly.
- Then, one question at a time: is an ambulance available? what type? estimated
  arrival time? any charges? Ask for explicit confirmation before treating it as
  booked, and capture a booking/reference number if they give one.
- Keep each message short and spoken-sounding (1–3 sentences). Be calm and polite.
- If they can't help, politely ask for the appropriate next step.
- Do NOT claim an ambulance is dispatched unless the receptionist confirms it.

# VERIFIED EMERGENCY DETAILS (the only facts you may share)
{facts}

Begin the call. After the receptionist responds, continue naturally until the
request is confirmed or declined, then thank them and close.
"""

_EXTRACT_SYSTEM = """\
You read a transcript of a phone call between an AI assistant (ResQ) arranging an
ambulance and a hospital receptionist. Extract ONLY what the receptionist actually
said. Output STRICT JSON with exactly these keys:
{
  "availability": "AVAILABLE" | "UNAVAILABLE" | "UNKNOWN",
  "ambulance_type": string | null,
  "eta": string | null,
  "charges": string | null,
  "booking_reference": string | null,
  "booking_confirmed": boolean,
  "declined": boolean,
  "summary": string
}
Rules: use null for anything the receptionist did not actually provide. Set
"booking_confirmed" true ONLY if the receptionist clearly agreed to send/arrange
an ambulance. Set "declined" true only if they refused or cannot help. "summary"
is one short sentence of the outcome. Output JSON only, no prose.
"""


def _facts_block(ctx: dict) -> str:
    def line(label: str, val) -> str | None:
        return f"- {label}: {val}" if val not in (None, "", 0) else None

    rows = [
        line("Hospital/service being called", ctx.get("service_name")),
        line("Emergency type", ctx.get("emergency_type")),
        line("Patient condition", ctx.get("patient_condition")),
        line("Patient name", ctx.get("patient_name")),
        line("Patient age", ctx.get("patient_age")),
        line("Number of patients", ctx.get("num_patients")),
        line("Pickup location", ctx.get("pickup_address")),
        line("Coordinates", ctx.get("coordinates")),
        line("Location accuracy (m)", ctx.get("location_accuracy_m")),
        line("Special requirements", ctx.get("special_requirements")),
    ]
    present = [r for r in rows if r]
    return "\n".join(present) or "- (no verified details available; say they are being gathered)"


def _contents(transcript: list[dict]) -> list[dict]:
    """Map the call transcript to Gemini contents. The AI coordinator is the
    'model'; the receptionist (human) is the 'user'. A kickoff user turn is added
    so the model produces the opening line when the call starts."""
    contents: list[dict] = []
    for m in transcript:
        text = (m.get("text") or "").strip()
        if not text:
            continue
        role = "model" if m.get("role") == "ai" else "user"
        contents.append({"role": role, "parts": [{"text": text}]})
    if not contents or contents[-1]["role"] == "model":
        # Nothing from the receptionist yet (or AI spoke last) → prompt the AI to
        # open / continue.
        contents.append(
            {"role": "user", "parts": [{"text": "[The receptionist has just answered the phone.]"}]}
        )
    return contents


async def coordinator_reply(ctx: dict, transcript: list[dict]) -> str:
    """Next line from the AI coordinator given the call so far."""
    system = _COORDINATOR_SYSTEM.format(facts=_facts_block(ctx))
    reply = await gemini_generate(system, _contents(transcript), temperature=0.5, max_output_tokens=350)
    return reply or "Hello, this is ResQ, an automated assistant calling about an emergency ambulance."


async def extract_outcome(transcript: list[dict]) -> dict:
    """Parse the finished call into a structured booking outcome (one JSON call)."""
    convo = "\n".join(
        f"{'ResQ(AI)' if m.get('role') == 'ai' else 'Receptionist'}: {m.get('text','').strip()}"
        for m in transcript
        if (m.get("text") or "").strip()
    )
    if not convo:
        return _empty_outcome()
    try:
        raw = await gemini_generate(
            _EXTRACT_SYSTEM,
            [{"role": "user", "parts": [{"text": convo}]}],
            temperature=0.0,
            max_output_tokens=400,
            response_json=True,
        )
        data = json.loads(raw)
    except (AgentError, json.JSONDecodeError, ValueError) as exc:
        logger.warning(f"[ambulance] outcome extraction failed: {exc}")
        return _empty_outcome()

    av = str(data.get("availability", "UNKNOWN")).upper()
    if av not in ("AVAILABLE", "UNAVAILABLE", "UNKNOWN"):
        av = "UNKNOWN"
    return {
        "availability": av,
        "ambulance_type": data.get("ambulance_type") or None,
        "eta": data.get("eta") or None,
        "charges": data.get("charges") or None,
        "booking_reference": data.get("booking_reference") or None,
        "booking_confirmed": bool(data.get("booking_confirmed")),
        "declined": bool(data.get("declined")),
        "summary": (data.get("summary") or "")[:500] or None,
    }


def _empty_outcome() -> dict:
    return {
        "availability": "UNKNOWN",
        "ambulance_type": None,
        "eta": None,
        "charges": None,
        "booking_reference": None,
        "booking_confirmed": False,
        "declined": False,
        "summary": None,
    }
