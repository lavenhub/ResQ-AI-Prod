"""Unit tests for the voice-agent tools (no LLM / no WebRTC).

We drive the handlers with a fake FunctionCallParams and assert the DB side
effects + idempotency + no-wipe behaviour.
"""

from __future__ import annotations

import asyncio

from app.db import session_scope
from app.incident_service import get_incident
from app.voice.tools import VoiceToolContext, VoiceTools


class FakeParams:
    """Mimics pipecat's FunctionCallParams for the bits the tools use."""

    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, result):
        self.result = result


def _run(coro):
    return asyncio.run(coro)


def _tools(session_id: str) -> VoiceTools:
    return VoiceTools(
        VoiceToolContext(
            session_id=session_id,
            user_ref="+911234567890",
            detected_language="hi-IN",
            latitude=19.07,
            longitude=72.87,
            location_accuracy=12.0,
            address="Bandra, Mumbai",
        )
    )


def test_create_then_read_from_db():
    tools = _tools("tool-sess-1")
    p = FakeParams({"incident_type": "Snakebite", "severity": "CRITICAL", "bleeding": "No"})
    _run(tools.create_emergency_incident(p))

    assert p.result["ok"] is True
    assert p.result["created"] is True
    # Honesty note must be present so the model never implies dispatch.
    assert "not been dispatched" in p.result["note"].lower()

    with session_scope() as s:
        inc = get_incident(s, p.result["incident_id"])
        assert inc is not None
        assert inc.incident_type == "Snakebite"
        assert inc.severity.value == "CRITICAL"
        # Location came from the device context, not the model.
        assert inc.latitude == 19.07
        assert inc.detected_language == "hi-IN"
        assert inc.user_ref == "+911234567890"


def test_create_is_idempotent_per_session():
    tools = _tools("tool-sess-2")
    p1 = FakeParams({"incident_type": "Fire"})
    _run(tools.create_emergency_incident(p1))
    # Simulate a retry / second tool call in the same session.
    tools2 = _tools("tool-sess-2")
    p2 = FakeParams({"incident_type": "Fire"})
    _run(tools2.create_emergency_incident(p2))
    assert p1.result["incident_id"] == p2.result["incident_id"]
    assert p2.result["created"] is False


def test_update_preserves_prior_fields():
    tools = _tools("tool-sess-3")
    _run(tools.create_emergency_incident(FakeParams({"incident_type": "Road Accident",
                                                     "description": "bike skid"})))
    # Update only severity.
    _run(tools.update_emergency_incident(FakeParams({"severity": "HIGH"})))
    with session_scope() as s:
        inc = get_incident(s, tools.ctx.incident_id)
        assert inc.severity.value == "HIGH"
        assert inc.description == "bike skid"  # not wiped
        assert inc.incident_type == "Road Accident"


def test_get_current_location_returns_device_fix():
    tools = _tools("tool-sess-4")
    p = FakeParams({})
    _run(tools.get_current_location(p))
    assert p.result["available"] is True
    assert p.result["latitude"] == 19.07
    assert p.result["address"] == "Bandra, Mumbai"


def test_get_location_unavailable():
    tools = VoiceTools(VoiceToolContext(session_id="tool-sess-5"))
    p = FakeParams({})
    _run(tools.get_current_location(p))
    assert p.result["available"] is False
