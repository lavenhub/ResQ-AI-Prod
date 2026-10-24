"""Tests for the live-retrieval layer — all HTTP is mocked.

We never hit a real API here. A fake `httpx.AsyncClient` lets us drive the exact
failure modes the task requires: success, timeout, rate-limit (429), malformed
JSON, provider down (all mirrors fail), stale-cache fallback, and "not
configured". The assertions prove the layer NEVER fabricates — it returns an
honest ok=False or a clearly-flagged stale result.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.config import get_settings
from app.services import retrieval


def _run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _reset():
    retrieval._reset_cache_for_tests()
    get_settings.cache_clear()
    yield
    retrieval._reset_cache_for_tests()
    get_settings.cache_clear()


# ── fake httpx ────────────────────────────────────────────────────────────────
class _FakeResp:
    def __init__(self, json_data=None, status=200, json_exc=None):
        self._json = json_data
        self.status_code = status
        self._json_exc = json_exc

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        if self._json_exc is not None:
            raise self._json_exc
        return self._json


class _FakeClient:
    """Dispatches GET/POST to a handler(method, url, params_or_data) -> _FakeResp
    or raises whatever the handler raises (to simulate timeouts)."""

    handler = None  # set per test

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        return _FakeClient.handler("GET", url, params)

    async def post(self, url, data=None):
        return _FakeClient.handler("POST", url, data)


@pytest.fixture
def fake_http(monkeypatch):
    monkeypatch.setattr(retrieval.httpx, "AsyncClient", _FakeClient)

    def _set(handler):
        _FakeClient.handler = staticmethod(handler)

    yield _set
    _FakeClient.handler = None


_OPEN_METEO_OK = {
    "current": {
        "time": "2026-10-21T12:45",
        "temperature_2m": 44.0,
        "apparent_temperature": 47.0,
        "weather_code": 95,  # thunderstorm
        "wind_speed_10m": 60.0,
        "relative_humidity_2m": 20,
        "precipitation": 0.0,
    },
    "daily": {
        "time": ["2026-10-21", "2026-10-22"],
        "weather_code": [95, 82],
        "temperature_2m_max": [45.0, 44.0],
        "precipitation_sum": [80.0, 10.0],
        "wind_speed_10m_max": [70.0, 40.0],
    },
}


# ── weather ─────────────────────────────────────────────────────────────────
def test_weather_success_parses_and_derives_hazards(fake_http):
    fake_http(lambda m, u, p: _FakeResp(_OPEN_METEO_OK))
    res = _run(retrieval.get_weather(19.07, 72.87))
    assert res.ok is True
    assert res.source.startswith("Open-Meteo")
    assert res.data_as_of == "2026-10-21T12:45"
    hz = res.data["hazards"]
    assert any("thunderstorm" in h for h in hz)
    assert any("extreme heat" in h for h in hz)
    assert any("high winds" in h for h in hz)
    # Must flag that these are derived, not official warnings.
    assert "not an official" in res.data["hazards_note"].lower()


def test_weather_caches_within_ttl(fake_http):
    calls = {"n": 0}

    def h(m, u, p):
        calls["n"] += 1
        return _FakeResp(_OPEN_METEO_OK)

    fake_http(h)
    r1 = _run(retrieval.get_weather(19.07, 72.87))
    r2 = _run(retrieval.get_weather(19.07, 72.87))
    assert calls["n"] == 1  # second served from cache
    assert r1.retrieved_at == r2.retrieved_at


def test_weather_timeout_returns_honest_failure(fake_http):
    def h(m, u, p):
        raise httpx.ReadTimeout("slow")

    fake_http(h)
    res = _run(retrieval.get_weather(1.0, 2.0))
    assert res.ok is False
    assert res.error == "weather_unavailable"
    assert res.data is None  # nothing fabricated


def test_weather_rate_limit_returns_honest_failure(fake_http):
    fake_http(lambda m, u, p: _FakeResp(status=429))
    res = _run(retrieval.get_weather(3.0, 4.0))
    assert res.ok is False
    assert res.data is None


def test_weather_malformed_json_returns_honest_failure(fake_http):
    fake_http(lambda m, u, p: _FakeResp(json_exc=ValueError("not json")))
    res = _run(retrieval.get_weather(5.0, 6.0))
    assert res.ok is False


def test_weather_stale_cache_fallback(fake_http, monkeypatch):
    # A negative TTL forces a refetch every call regardless of clock resolution,
    # so the first (good) result is only available as a STALE fallback when the
    # refetch fails. (TTL 0 is racy on Windows' coarse monotonic() resolution.)
    monkeypatch.setenv("WEATHER_CACHE_SECONDS", "-1")
    get_settings.cache_clear()

    fake_http(lambda m, u, p: _FakeResp(_OPEN_METEO_OK))
    first = _run(retrieval.get_weather(7.0, 8.0))
    assert first.ok is True

    def boom(m, u, p):
        raise httpx.ConnectError("down")

    fake_http(boom)
    second = _run(retrieval.get_weather(7.0, 8.0))
    assert second.stale is True
    assert second.ok is True  # it's the previous good value, flagged stale
    assert "cached" in (second.note or "").lower()


# ── hospitals ─────────────────────────────────────────────────────────────────
_OVERPASS_OK = {
    "elements": [
        {"type": "node", "lat": 19.071, "lon": 72.871,
         "tags": {"name": "Near Clinic", "amenity": "hospital", "phone": "+91-22-111"}},
        {"type": "way", "center": {"lat": 19.10, "lon": 72.90},
         "tags": {"name": "Far Hospital", "amenity": "hospital"}},
        {"type": "node", "lat": 19.072, "lon": 72.872,
         "tags": {"amenity": "hospital"}},  # no name -> dropped
    ]
}


def test_hospitals_success_sorts_by_distance_and_drops_nameless(fake_http):
    fake_http(lambda m, u, d: _FakeResp(_OVERPASS_OK))
    res = _run(retrieval.find_hospitals(19.07, 72.87, max_results=5))
    assert res.ok is True
    names = [h["name"] for h in res.data["hospitals"]]
    assert names == ["Near Clinic", "Far Hospital"]  # nameless dropped, nearest first
    assert res.data["hospitals"][0]["phone"] == "+91-22-111"
    assert "call ahead" in (res.note or "").lower()


def test_hospitals_all_mirrors_fail_degrades_honestly(fake_http):
    def boom(m, u, d):
        raise httpx.ReadTimeout("overpass down")

    fake_http(boom)
    res = _run(retrieval.find_hospitals(19.07, 72.87))
    assert res.ok is False
    assert res.error == "hospitals_unavailable"
    # Safe fallback instruction, not a fabricated list.
    assert "emergency number" in (res.note or "").lower()
    assert res.data is None


def test_hospitals_max_results_is_bounded(fake_http):
    many = {"elements": [
        {"type": "node", "lat": 19.07 + i / 1000, "lon": 72.87,
         "tags": {"name": f"H{i}", "amenity": "hospital"}}
        for i in range(30)
    ]}
    fake_http(lambda m, u, d: _FakeResp(many))
    res = _run(retrieval.find_hospitals(19.07, 72.87, max_results=99))
    assert len(res.data["hospitals"]) == 10  # clamped to 10


# ── advisories ─────────────────────────────────────────────────────────────────
def test_advisories_not_configured_is_honest(fake_http, monkeypatch):
    monkeypatch.setenv("DISASTER_ADVISORY_URL", "")
    get_settings.cache_clear()
    res = _run(retrieval.get_advisories(19.07, 72.87))
    assert res.ok is False
    assert res.error == "not_configured"
    assert "cannot confirm" in (res.note or "").lower()


def test_advisories_configured_success(fake_http, monkeypatch):
    monkeypatch.setenv("DISASTER_ADVISORY_URL", "https://example.test/advisories")
    get_settings.cache_clear()
    payload = {"advisories": [{"title": "Flood warning", "severity": "high"}]}
    fake_http(lambda m, u, p: _FakeResp(payload))
    res = _run(retrieval.get_advisories(19.07, 72.87))
    assert res.ok is True
    assert res.data == payload


def test_advisories_configured_failure_is_honest(fake_http, monkeypatch):
    monkeypatch.setenv("DISASTER_ADVISORY_URL", "https://example.test/advisories")
    get_settings.cache_clear()

    def boom(m, u, p):
        raise httpx.ConnectError("feed down")

    fake_http(boom)
    res = _run(retrieval.get_advisories(19.07, 72.87))
    assert res.ok is False
    assert res.error == "advisory_unavailable"
    assert res.data is None


# ── provenance / helpers ────────────────────────────────────────────────────
def test_result_to_tool_dict_carries_provenance(fake_http):
    fake_http(lambda m, u, p: _FakeResp(_OPEN_METEO_OK))
    d = _run(retrieval.get_weather(19.07, 72.87)).to_tool_dict()
    assert d["ok"] is True
    assert "source" in d and "retrieved_at" in d
    assert d["data_as_of"] == "2026-10-21T12:45"


def test_failed_result_dict_has_no_fabricated_data(fake_http):
    fake_http(lambda m, u, p: _FakeResp(status=500))
    d = _run(retrieval.get_weather(19.07, 72.87)).to_tool_dict()
    assert d["ok"] is False
    assert "data" not in d  # never emit a data blob on failure


def test_haversine_is_reasonable():
    # ~111 km per degree of latitude.
    d = retrieval.haversine_m(19.0, 72.0, 20.0, 72.0)
    assert 110_000 < d < 112_000
