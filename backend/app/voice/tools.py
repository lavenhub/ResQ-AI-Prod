"""Voice-agent tools.

These are the ONLY actions the LLM can take on the backend. Each is:
  * schema-constrained (Gemini can only send these fields),
  * validated again server-side via the Pydantic `IncidentCreate/Update` models,
  * scoped to THIS session's incident (the model can't address other incidents),
  * idempotent (one incident per session; retries don't duplicate).

Location and language are injected from the session context, never taken from
the model — so the agent cannot fabricate coordinates.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from .. import contact_service, incident_service
from ..config import get_settings
from ..db import session_scope
from ..models import IncidentCreate, IncidentUpdate, SosRequest, TriggerSource
from ..services import emergency_reference, retrieval
from ..services.notifier import summarize

_SEVERITY = ["CRITICAL", "HIGH", "MEDIUM", "LOW", "UNKNOWN"]
_TRISTATE = ["Yes", "No", "Unknown"]

# The incident fields the model may set (location/language are injected, not here).
_MODEL_FIELDS = (
    "incident_type",
    "description",
    "reported_symptoms",
    "reported_hazards",
    "severity",
    "conscious",
    "breathing",
    "bleeding",
)


def _provided(args: dict) -> dict:
    """Only the fields the model actually sent (drop missing/null)."""
    return {k: args[k] for k in _MODEL_FIELDS if k in args and args[k] is not None}

# Fields the model is allowed to set on an incident (location/language excluded).
_INCIDENT_PROPERTIES = {
    "incident_type": {
        "type": "string",
        "description": "Short label, e.g. 'Snakebite', 'Road Accident', 'Fire', "
        "'Heart Attack', 'Bleeding', 'Fall', 'Choking'.",
    },
    "description": {
        "type": "string",
        "description": "One or two short sentences on what happened, in English.",
    },
    "reported_symptoms": {
        "type": "string",
        "description": "Symptoms the caller actually reported. Leave unset if none given.",
    },
    "reported_hazards": {
        "type": "string",
        "description": "Immediate dangers at the scene (traffic, fire, water, gas).",
    },
    "severity": {"type": "string", "enum": _SEVERITY, "description": "Your best triage level."},
    "conscious": {"type": "string", "enum": _TRISTATE},
    "breathing": {"type": "string", "enum": _TRISTATE},
    "bleeding": {"type": "string", "enum": _TRISTATE},
}


@dataclass
class VoiceToolContext:
    """Per-session state shared with the tool handlers."""

    session_id: str
    user_ref: str | None = None
    detected_language: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None
    incident_id: str | None = field(default=None)


class VoiceTools:
    def __init__(self, ctx: VoiceToolContext) -> None:
        self.ctx = ctx

    # ── tool: create ──────────────────────────────────────────────────────
    async def create_emergency_incident(self, params: FunctionCallParams) -> None:
        args = params.arguments or {}
        try:
            data = IncidentCreate(
                session_id=self.ctx.session_id,
                user_ref=self.ctx.user_ref,
                detected_language=self.ctx.detected_language,
                latitude=self.ctx.latitude,
                longitude=self.ctx.longitude,
                location_accuracy=self.ctx.location_accuracy,
                address=self.ctx.address,
                **_provided(args),
            )
        except Exception as e:  # validation error -> tell the model, don't crash
            logger.warning(f"[tools] create validation failed: {e}")
            await params.result_callback(
                {"ok": False, "error": "invalid_arguments", "detail": str(e)}
            )
            return

        def _work() -> tuple[str, bool]:
            with session_scope() as s:
                incident, created = incident_service.create_incident(s, data)
                return incident.id, created

        incident_id, created = await asyncio.to_thread(_work)
        self.ctx.incident_id = incident_id
        logger.info(f"[tools] incident {'created' if created else 'reused'}: {incident_id}")
        await params.result_callback(
            {
                "ok": True,
                "incident_id": incident_id,
                "created": created,
                # Reinforce honesty back to the model:
                "note": "Incident recorded. Help has NOT been dispatched — advise the "
                "caller to phone 108/112 or have someone nearby do so.",
            }
        )

    # ── tool: update ──────────────────────────────────────────────────────
    async def update_emergency_incident(self, params: FunctionCallParams) -> None:
        args = params.arguments or {}
        if not self.ctx.incident_id:
            # No incident yet — create one first from whatever we have.
            await self.create_emergency_incident(params)
            return
        try:
            data = IncidentUpdate(**_provided(args))
        except Exception as e:
            logger.warning(f"[tools] update validation failed: {e}")
            await params.result_callback(
                {"ok": False, "error": "invalid_arguments", "detail": str(e)}
            )
            return

        incident_id = self.ctx.incident_id

        def _work() -> bool:
            with session_scope() as s:
                updated = incident_service.update_incident(s, incident_id, data)
                return updated is not None

        ok = await asyncio.to_thread(_work)
        await params.result_callback({"ok": ok, "incident_id": incident_id})

    # ── tool: location ────────────────────────────────────────────────────
    async def get_current_location(self, params: FunctionCallParams) -> None:
        c = self.ctx
        if c.latitude is None or c.longitude is None:
            await params.result_callback(
                {"available": False, "note": "Device location not available; ask the caller."}
            )
            return
        await params.result_callback(
            {
                "available": True,
                "latitude": c.latitude,
                "longitude": c.longitude,
                "accuracy_m": c.location_accuracy,
                "address": c.address,
            }
        )

    # ── tool: trigger email alerts to the caller's saved contacts ──────────
    async def trigger_sos_alert(self, params: FunctionCallParams) -> None:
        """Send the emergency EMAIL to the caller's OWN saved, verified contacts.

        Guardrails baked in (the model cannot bypass them):
          * requires an authenticated session (user_ref from the signed token);
            an anonymous call cannot trigger alerts.
          * recipients come ONLY from the user's stored, verified contacts — the
            model never supplies email addresses.
          * idempotent per voice session, so re-calling it never double-sends.
          * returns an HONEST status (recorded / queued), never "delivered".
        """
        c = self.ctx
        if not c.user_ref:
            await params.result_callback(
                {
                    "ok": False,
                    "error": "not_authenticated",
                    "note": "This call isn't linked to a signed-in account, so I can't "
                    "send alerts to saved contacts. Tell the caller to call 112/108 and, "
                    "if they can, open the ResQ app signed in.",
                }
            )
            return

        args = params.arguments or {}
        data = SosRequest(
            incident_type=args.get("incident_type"),
            note=args.get("note"),
            victim_name=args.get("victim_name"),
            latitude=c.latitude,
            longitude=c.longitude,
            location_accuracy=c.location_accuracy,
            address=c.address,
            # One idempotency key per voice session => at most one alert wave.
            idempotency_key=f"voice-{c.session_id}",
        )

        def _work():
            with session_scope() as s:
                contacts = contact_service.eligible_contacts(s, c.user_ref)  # type: ignore[arg-type]
                if not contacts:
                    return ("no_contacts", None, None)
                incident, jobs, created = incident_service.create_sos_incident(
                    s,
                    user_ref=c.user_ref,  # type: ignore[arg-type]
                    data=data,
                    victim_name=args.get("victim_name"),
                    contacts=contacts,
                    trigger_source=TriggerSource.VOICE,
                )
                return ("ok", incident.id, summarize(jobs))

        status, incident_id, summary = await asyncio.to_thread(_work)
        if status == "no_contacts":
            await params.result_callback(
                {
                    "ok": False,
                    "error": "no_contacts",
                    "note": "The caller has no verified emergency contacts to alert. Advise "
                    "them to call 112/108 and reach someone nearby.",
                }
            )
            return
        self.ctx.incident_id = incident_id
        logger.info(f"[tools] voice SOS alert queued for incident {incident_id}")
        await params.result_callback(
            {
                "ok": True,
                "incident_id": incident_id,
                "recipients_queued": summary.get("total") if summary else 0,
                # Honesty: queued is NOT delivered.
                "note": "Incident recorded and EMAIL alerts QUEUED to the caller's saved, "
                "verified contacts. I cannot yet confirm the messages were delivered. No "
                "rescue vehicle has been dispatched — advise the caller to call 112/108.",
            }
        )

    # ── tool: official emergency numbers (stable reference, not live) ──────
    async def get_local_emergency_info(self, params: FunctionCallParams) -> None:
        args = params.arguments or {}
        category = args.get("category")
        # emergency_numbers_for normalises an unknown/garbage category to "general".
        info = emergency_reference.emergency_numbers_for(
            category, country=get_settings().default_country
        )
        await params.result_callback(info)

    # ── tool: live weather + derived hazards ───────────────────────────────
    async def get_weather_alerts(self, params: FunctionCallParams) -> None:
        if not await self._retrieval_ready(params):
            return
        result = await retrieval.get_weather(self.ctx.latitude, self.ctx.longitude)  # type: ignore[arg-type]
        await params.result_callback(result.to_tool_dict())

    # ── tool: nearby hospitals from map data ───────────────────────────────
    async def find_nearby_hospitals(self, params: FunctionCallParams) -> None:
        if not await self._retrieval_ready(params):
            return
        args = params.arguments or {}
        try:
            max_results = int(args.get("max_results", 5))
        except (TypeError, ValueError):
            max_results = 5
        result = await retrieval.find_hospitals(
            self.ctx.latitude,  # type: ignore[arg-type]
            self.ctx.longitude,  # type: ignore[arg-type]
            max_results=max_results,
        )
        await params.result_callback(result.to_tool_dict())

    # ── tool: official disaster advisories (config-driven) ─────────────────
    async def get_disaster_advisories(self, params: FunctionCallParams) -> None:
        if not await self._retrieval_ready(params):
            return
        result = await retrieval.get_advisories(
            self.ctx.latitude, self.ctx.longitude  # type: ignore[arg-type]
        )
        await params.result_callback(result.to_tool_dict())

    # ── shared guard for the location-dependent live tools ─────────────────
    async def _retrieval_ready(self, params: FunctionCallParams) -> bool:
        """Return True if a live lookup can proceed; otherwise answer honestly.

        Two honest failure modes, surfaced to the model (never faked): retrieval
        disabled on the server, or no device location to anchor the lookup. On
        False the honest result has already been sent to the model.
        """
        if not get_settings().enable_retrieval:
            await params.result_callback(
                {"ok": False, "error": "retrieval_disabled",
                 "note": "Live lookups are turned off on this server."}
            )
            return False
        if self.ctx.latitude is None or self.ctx.longitude is None:
            await params.result_callback(
                {"ok": False, "error": "no_location",
                 "note": "I don't have your location yet — tell me where you are."}
            )
            return False
        return True

    # ── wiring ────────────────────────────────────────────────────────────
    def register(self, llm) -> None:
        llm.register_function("create_emergency_incident", self.create_emergency_incident)
        llm.register_function("update_emergency_incident", self.update_emergency_incident)
        llm.register_function("trigger_sos_alert", self.trigger_sos_alert)
        llm.register_function("get_current_location", self.get_current_location)
        llm.register_function("get_local_emergency_info", self.get_local_emergency_info)
        llm.register_function("get_weather_alerts", self.get_weather_alerts)
        llm.register_function("find_nearby_hospitals", self.find_nearby_hospitals)
        llm.register_function("get_disaster_advisories", self.get_disaster_advisories)


def build_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="create_emergency_incident",
                description="Record a new emergency incident for this call as soon as you "
                "know the type. Call once; later details go through update.",
                properties=_INCIDENT_PROPERTIES,
                required=["incident_type"],
            ),
            FunctionSchema(
                name="update_emergency_incident",
                description="Add or revise details on the incident already recorded for "
                "this call (symptoms, hazards, severity, vitals).",
                properties=_INCIDENT_PROPERTIES,
                required=[],
            ),
            FunctionSchema(
                name="trigger_sos_alert",
                description="Send an emergency EMAIL to the caller's OWN saved, verified "
                "emergency contacts. ONLY call this AFTER the caller clearly confirms they "
                "want their contacts alerted (ask first). You never provide email addresses "
                "— they come from the caller's saved contacts. Safe to call once; it will "
                "not double-send. Returns whether alerts were QUEUED (not proof of "
                "delivery).",
                properties={
                    "incident_type": {
                        "type": "string",
                        "description": "Short label of the emergency, e.g. 'Road Accident'.",
                    },
                    "victim_name": {
                        "type": "string",
                        "description": "The caller's name if they gave it, for the alert text.",
                    },
                    "note": {
                        "type": "string",
                        "description": "One short, safe line of extra context for contacts.",
                    },
                },
                required=[],
            ),
            FunctionSchema(
                name="get_current_location",
                description="Get the caller's device-reported location (lat/lng/address).",
                properties={},
                required=[],
            ),
            FunctionSchema(
                name="get_local_emergency_info",
                description="Get the official emergency helpline number(s) for the "
                "caller's area and the type of emergency. Use this to give the exact "
                "right number instead of guessing. Returns stable reference data.",
                properties={
                    "category": {
                        "type": "string",
                        "enum": list(emergency_reference.CATEGORIES),
                        "description": "Best-matching emergency category; use 'general' "
                        "if unsure (112 works for everything in India).",
                    }
                },
                required=[],
            ),
            FunctionSchema(
                name="get_weather_alerts",
                description="Get CURRENT weather and derived weather hazards (heat, "
                "storms, heavy rain, flooding risk) for the caller's location. Use for "
                "heat, exposure, floods, storms or a stranded caller. Live data — do "
                "not guess the weather.",
                properties={},
                required=[],
            ),
            FunctionSchema(
                name="find_nearby_hospitals",
                description="Find hospitals near the caller from map data (name, "
                "distance, phone if known). Tell the caller to phone ahead — you "
                "cannot confirm a hospital is open or has capacity. For an ambulance, "
                "the emergency number is faster.",
                properties={
                    "max_results": {
                        "type": "integer",
                        "description": "How many nearby hospitals to return (1-10).",
                    }
                },
                required=[],
            ),
            FunctionSchema(
                name="get_disaster_advisories",
                description="Get official disaster/public-safety advisories for the "
                "caller's area, IF a feed is configured on the server. If not "
                "configured it says so honestly — do not invent advisories.",
                properties={},
                required=[],
            ),
        ]
    )
