"""Tests for the hosted voice-agent config passthrough (/api/resqai/config)."""

from __future__ import annotations

import os

from app.config import get_settings


def test_resqai_config_503_when_unconfigured(client):
    # No RESQAI_AGENT_URL set => honest "not configured", never a blank success.
    r = client.get("/api/resqai/config")
    assert r.status_code == 503
    body = r.json()
    assert body["configured"] is False
    assert r.headers["cache-control"] == "no-store"


def test_resqai_config_returns_url_when_configured(client):
    url = "https://app.toughtongueai.com/embed/testscenario?bg=black&skipPrecheck=true"
    os.environ["RESQAI_AGENT_URL"] = url
    get_settings.cache_clear()
    try:
        r = client.get("/api/resqai/config")
        assert r.status_code == 200
        body = r.json()
        assert body["configured"] is True
        assert body["agentUrl"] == url
        # A token-bearing URL must not be stored in shared caches.
        assert r.headers["cache-control"] == "no-store"
    finally:
        os.environ.pop("RESQAI_AGENT_URL", None)
        get_settings.cache_clear()
