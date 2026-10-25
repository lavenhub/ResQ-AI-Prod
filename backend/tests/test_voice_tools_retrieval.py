"""Tests for the retrieval-backed voice tools and the overall tool contract.

Retrieval itself is tested in test_retrieval.py; here we check the TOOL layer:
coordinate injection, honest guards (no location / retrieval disabled), argument
bounding, and — importantly — that the agent is NOT given any tool that could let
it falsely claim it dispatched help or notified someone.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config import get_settings
from app.services import retrieval
from app.services.retrieval import RetrievalResult
from app.voice.tools import VoiceToolContext, VoiceTools, build_tools_schema


class FakeParams:
    def __init__(self, arguments: dict):
        self.arguments = arguments
        self.result = None

    async def result_callback(self, result):
        self.result = result


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _ctx_with_loc() -> VoiceToolContext:
    return VoiceToolContext(session_id="rt-1", latitude=19.07, longitude=72.87,
                            address="Mumbai")


# ── emergency info (no network) ──────────────────────────────────────────────
def test_get_local_emergency_info_fire():
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({"category": "fire"})
    _run(t.get_local_emergency_info(p))
    numbers = [n["number"] for n in p.result["numbers"]]
    assert "112" in numbers and "101" in numbers
    assert p.result["kind"] == "official_reference"


def test_get_local_emergency_info_bad_category_is_general():
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({"category": "???"})
    _run(t.get_local_emergency_info(p))
    assert p.result["category"] == "general"


# ── weather tool (retrieval mocked) ──────────────────────────────────────────
def test_weather_tool_uses_session_coords(monkeypatch):
    seen = {}

    async def fake_get_weather(lat, lng):
        seen["lat"], seen["lng"] = lat, lng
        return RetrievalResult(ok=True, source="fake", retrieved_at="2026-01-01T00:00:00",
                               data={"conditions": "clear", "hazards": []})

    monkeypatch.setattr(retrieval, "get_weather", fake_get_weather)
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({})
    _run(t.get_weather_alerts(p))
    assert seen == {"lat": 19.07, "lng": 72.87}
    assert p.result["ok"] is True
    assert p.result["source"] == "fake"


def test_weather_tool_without_location_is_honest():
    t = VoiceTools(VoiceToolContext(session_id="noloc"))
    p = FakeParams({})
    _run(t.get_weather_alerts(p))
    assert p.result["ok"] is False
    assert p.result["error"] == "no_location"


def test_retrieval_disabled_is_honest(monkeypatch):
    monkeypatch.setenv("ENABLE_RETRIEVAL", "false")
    get_settings.cache_clear()
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({})
    _run(t.get_weather_alerts(p))
    assert p.result["ok"] is False
    assert p.result["error"] == "retrieval_disabled"


# ── hospitals tool (retrieval mocked) ────────────────────────────────────────
def test_hospitals_tool_bounds_and_passes_args(monkeypatch):
    seen = {}

    async def fake_find(lat, lng, *, max_results=5):
        seen["max_results"] = max_results
        return RetrievalResult(ok=True, source="fake", retrieved_at="t",
                               data={"hospitals": [], "count": 0})

    monkeypatch.setattr(retrieval, "find_hospitals", fake_find)
    t = VoiceTools(_ctx_with_loc())
    _run(t.find_nearby_hospitals(FakeParams({"max_results": 3})))
    assert seen["max_results"] == 3


def test_hospitals_tool_bad_arg_defaults(monkeypatch):
    async def fake_find(lat, lng, *, max_results=5):
        return RetrievalResult(ok=True, source="fake", retrieved_at="t",
                               data={"hospitals": [], "max_results": max_results})

    monkeypatch.setattr(retrieval, "find_hospitals", fake_find)
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({"max_results": "lots"})  # unparseable -> default 5
    _run(t.find_nearby_hospitals(p))
    assert p.result["ok"] is True


# ── advisories ────────────────────────────────────────────────────────────────
def test_advisories_tool_unconfigured_is_honest(monkeypatch):
    monkeypatch.setenv("DISASTER_ADVISORY_URL", "")
    get_settings.cache_clear()
    t = VoiceTools(_ctx_with_loc())
    p = FakeParams({})
    _run(t.get_disaster_advisories(p))
    assert p.result["ok"] is False
    assert p.result["error"] == "not_configured"


# ── the overall tool contract ────────────────────────────────────────────────
def test_tool_set_is_exactly_the_expected_eight():
    names = {fs.name for fs in build_tools_schema().standard_tools}
    assert names == {
        "create_emergency_incident",
        "update_emergency_incident",
        "trigger_sos_alert",
        "get_current_location",
        "get_local_emergency_info",
        "get_weather_alerts",
        "find_nearby_hospitals",
        "get_disaster_advisories",
    }


def test_agent_cannot_dispatch_vehicles_or_supply_recipients():
    # The agent CAN queue EMAIL to the caller's OWN saved contacts (trigger_sos_alert),
    # but it must NOT be able to dispatch vehicles, phone emergency services, or
    # supply arbitrary recipients to message.
    schema = {fs.name: fs for fs in build_tools_schema().standard_tools}
    # No vehicle-dispatch / call-service tool exists.
    forbidden = ("dispatch", "ambulance", "call_", "share_location", "911", "108")
    for n in schema:
        assert not any(f in n for f in forbidden), f"unexpected action tool: {n}"
    # The one alerting tool takes NO recipient argument — recipients are
    # resolved server-side from the authenticated user's saved, verified contacts.
    sos = schema["trigger_sos_alert"]
    props = set(getattr(sos, "properties", {}) or {})
    assert not (props & {"phone", "mobiles", "number", "to", "recipients",
                         "contacts", "email", "emails", "addresses"}), (
        "trigger_sos_alert must not accept caller-supplied recipients"
    )


def test_trigger_sos_alert_refuses_without_authenticated_user():
    # An anonymous voice session (no user_ref) cannot alert anyone.
    t = VoiceTools(VoiceToolContext(session_id="anon-sess"))
    p = FakeParams({"incident_type": "Fire"})
    _run(t.trigger_sos_alert(p))
    assert p.result["ok"] is False
    assert p.result["error"] == "not_authenticated"


def test_registered_functions_match_schema(monkeypatch):
    # Every schema tool is actually registered on the LLM service.
    registered = []

    class FakeLLM:
        def register_function(self, name, fn):
            registered.append(name)

    VoiceTools(_ctx_with_loc()).register(FakeLLM())
    assert set(registered) == {fs.name for fs in build_tools_schema().standard_tools}
