"""Tests for the HTTP conversational agent endpoint (/api/agent/chat).

No real Gemini call is made: the provider layer (app.agent.agent_reply) is
monkeypatched. We assert the honest 503 (no key), input validation, and that a
successful reply is returned.
"""

from __future__ import annotations

import app.routes.agent as agent_route


def test_chat_503_without_key(client):
    # Tests run with GEMINI_API_KEY="" => assistant not configured => 503.
    r = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 503


def test_chat_requires_messages(client):
    r = client.post("/api/agent/chat", json={"messages": []})
    assert r.status_code == 422


class _FakeSettings:
    gemini_api_key = "test-key"


def test_chat_returns_reply(client, monkeypatch):
    # Pretend a key is configured and stub the provider so no network happens.
    monkeypatch.setattr(agent_route, "get_settings", lambda: _FakeSettings())

    async def _fake_reply(messages, *, language=None, location_hint=None):
        assert messages and messages[-1]["content"] == "a snake bit me"
        return "Stay calm and still. Call 112 now. Where were you bitten?"

    monkeypatch.setattr(agent_route, "agent_reply", _fake_reply)

    r = client.post(
        "/api/agent/chat",
        json={
            "messages": [{"role": "user", "content": "a snake bit me"}],
            "language": "English",
            "lat": 19.07,
            "lng": 72.87,
        },
    )
    assert r.status_code == 200, r.text
    assert "Call 112" in r.json()["reply"]


def test_chat_502_on_provider_error(client, monkeypatch):
    monkeypatch.setattr(agent_route, "get_settings", lambda: _FakeSettings())

    async def _boom(messages, **kwargs):
        raise agent_route.AgentError("provider_error_500")

    monkeypatch.setattr(agent_route, "agent_reply", _boom)
    r = client.post("/api/agent/chat", json={"messages": [{"role": "user", "content": "hi"}]})
    assert r.status_code == 502
