"""Conversational emergency-assistant agent over plain HTTP (no WebRTC).

Calls Google Gemini's ``generateContent`` REST endpoint with the ResQ persona and
the running conversation, and returns a text reply. This runs anywhere a normal
outbound HTTPS request works — including Render — because it is ordinary
request/response, not real-time media. The browser handles speech (text-to-speech
for replies, optional speech-to-text for input), so no server media is involved.

Honesty rules mirror the rest of ResQAI: the assistant gives safe first-aid
guidance, tells the caller to phone 112/108, and NEVER claims it dispatched a
vehicle. The app separately (a) emails the caller's verified contacts (real) and
(b) shows a clearly-labelled *simulated* ambulance — the assistant does not
pretend those are a real dispatch.
"""

from __future__ import annotations

import httpx
from loguru import logger

from .config import get_settings
from .voice.scenarios import quick_reference_block

_GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

_SYSTEM = """\
You are "ResQ", a calm, trained emergency-response assistant for India (but you
help anyone). You are chatting in the ResQAI app with someone who may be in
danger, injured, panicked, or alone. Lives may depend on you being fast, calm,
accurate, and honest. Your replies are shown as chat and also read aloud, so keep
them short, clear, and spoken-sounding.

# LANGUAGE
- Reply in {language} unless the person clearly switches. Mirror Hindi/Marathi/
  English code-switching. Short, simple sentences; avoid jargon.

# HOW TO RESPOND (every turn)
1) If there is immediate danger (not breathing/responding, severe bleeding,
   choking, chest pain, stroke signs, fire/gas/water/violence, trapped): give the
   single most important action FIRST and tell them to call 112 / 108 now.
2) Then one brief reason only if it helps them act.
3) Then ONE most-important question. One question at a time — do not interrogate.
4) Reassure briefly ("I'm here with you"). Never a wall of text.

# WHAT THE APP DOES (so you can be honest about it)
- When a genuine emergency is detected, the ResQAI app ALERTS the person's saved,
  verified emergency contacts by EMAIL, and shows a clearly-labelled SIMULATED
  ambulance on the map for the demo. You may tell them their contacts are being
  notified. You must NEVER say a real ambulance/police/fire unit has been
  dispatched or is on the way — that would be false. Always steer them to call
  112 / 108 (or have someone nearby call) for real dispatch.

# SAFETY
- Give only widely-accepted first aid. Do not diagnose definitively, prescribe
  medicines/dosages, or suggest unproven remedies. Adapt to infants, children,
  pregnancy, elderly, disability. Never put the rescuer in danger (fire, gas,
  electrical, water, violence, unstable structures — get to safety first).
- Never invent facts (a location, a hospital's status, a response time). If you
  are unsure, say so and fall back to safe general guidance + call 112/108.

# HIGH-RISK QUICK REFERENCE (say the action; hold the do-NOTs firmly)
{quick_reference}

Keep every reply brief enough to be read aloud in a few seconds.
"""


def build_agent_system(language: str | None, location_hint: str | None) -> str:
    text = _SYSTEM.format(
        language=(language or "English"),
        quick_reference=quick_reference_block(),
    )
    if location_hint:
        text += (
            f"\nThe device reports the person's location as: {location_hint}. "
            "Treat it as a starting point to confirm, not a certainty."
        )
    return text


def _to_contents(messages: list[dict]) -> list[dict]:
    """Map [{role:'user'|'assistant', content}] -> Gemini contents (user/model)."""
    contents: list[dict] = []
    for m in messages:
        text = (m.get("content") or "").strip()
        if not text:
            continue
        role = "user" if m.get("role") == "user" else "model"
        contents.append({"role": role, "parts": [{"text": text}]})
    return contents


class AgentError(RuntimeError):
    pass


async def gemini_generate(
    system: str,
    contents: list[dict],
    *,
    temperature: float = 0.6,
    max_output_tokens: int | None = None,
    response_json: bool = False,
) -> str:
    """Low-level Gemini ``generateContent`` call shared by the chat assistant, the
    ambulance coordinator, and the outcome extractor. Returns the text (or raw
    JSON string when ``response_json``). Raises AgentError on any failure."""
    settings = get_settings()
    if not settings.gemini_api_key:
        raise AgentError("agent_not_configured")
    if not contents:
        raise AgentError("no_messages")

    gen_cfg: dict = {
        "temperature": temperature,
        "maxOutputTokens": max_output_tokens or settings.agent_max_output_tokens,
    }
    if response_json:
        gen_cfg["responseMimeType"] = "application/json"

    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": gen_cfg,
    }
    url = _GEMINI_URL.format(model=settings.agent_model)
    try:
        async with httpx.AsyncClient(timeout=settings.agent_timeout_seconds) as client:
            resp = await client.post(
                url,
                headers={
                    "x-goog-api-key": settings.gemini_api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
    except httpx.HTTPError as exc:
        logger.warning(f"[agent] network error: {exc}")
        raise AgentError("network_error") from exc

    if resp.status_code >= 400:
        logger.warning(f"[agent] gemini {resp.status_code}: {resp.text[:300]}")
        raise AgentError(f"provider_error_{resp.status_code}")

    data = resp.json()
    cands = data.get("candidates") or []
    if not cands:
        raise AgentError("no_candidates")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


async def agent_reply(
    messages: list[dict], *, language: str | None = None, location_hint: str | None = None
) -> str:
    """Return the assistant's reply for the given conversation. Raises AgentError
    on a provider failure; the caller maps that to an HTTP error."""
    reply = await gemini_generate(
        build_agent_system(language, location_hint), _to_contents(messages)
    )
    return reply or "I'm here with you. Tell me what's happening."
