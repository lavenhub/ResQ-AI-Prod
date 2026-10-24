"""Live retrieval layer for time-sensitive emergency facts.

The design rule for ResQAI is: behavioural knowledge lives in the prompt; *changing
facts* are retrieved. This module is that retrieval path. It wraps free, no-key,
authoritative-ish public APIs and returns results the LLM can quote with honest
provenance:

  * `get_weather(lat, lng)`       — Open-Meteo current conditions + near-term
                                     forecast, with derived weather hazards.
  * `find_hospitals(lat, lng)`    — OpenStreetMap (Overpass) nearby hospitals.
  * `get_advisories(lat, lng)`    — optional official advisory feed (config-driven).

Every result is a `RetrievalResult` carrying `source`, `retrieved_at`,
`data_as_of` and a `stale` flag. On timeout / failure / rate-limit the layer
NEVER fabricates: it returns `ok=False` (or a clearly-flagged stale cached value)
with a short note the agent can relay. Caches are short (seconds–minutes) because
emergency conditions change fast.

Nothing here is imported by frontend code; all network egress is server-side.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httpx
from loguru import logger

from ..config import get_settings


# ── Result envelope ───────────────────────────────────────────────────────────
@dataclass
class RetrievalResult:
    """A single retrieval outcome with explicit provenance.

    `to_tool_dict()` is what the LLM sees; it keeps provenance so the agent can be
    honest ("as of 12:45, from Open-Meteo") and never passes off stale or failed
    data as current.
    """

    ok: bool
    source: str
    retrieved_at: str  # ISO-8601 UTC — when WE fetched it
    data: Any = None
    data_as_of: str | None = None  # ISO-8601 — the period/time the data describes
    stale: bool = False
    error: str | None = None
    note: str | None = None

    def to_tool_dict(self) -> dict:
        out: dict[str, Any] = {
            "ok": self.ok,
            "source": self.source,
            "retrieved_at": self.retrieved_at,
        }
        if self.data_as_of is not None:
            out["data_as_of"] = self.data_as_of
        if self.stale:
            out["stale"] = True
        if self.error is not None:
            out["error"] = self.error
        if self.note is not None:
            out["note"] = self.note
        if self.data is not None:
            out["data"] = self.data
        return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── Tiny TTL cache with stale fallback ─────────────────────────────────────────
@dataclass
class _CacheEntry:
    result: RetrievalResult
    fetched_monotonic: float


class _TTLCache:
    """Async-safe cache keyed by string. Serves fresh within TTL; otherwise the
    caller re-fetches and, on failure, may fall back to the stale entry."""

    def __init__(self) -> None:
        self._store: dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    async def get_fresh(self, key: str, ttl: float) -> RetrievalResult | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            if (time.monotonic() - entry.fetched_monotonic) <= ttl:
                return entry.result
            return None

    async def get_any(self, key: str) -> RetrievalResult | None:
        async with self._lock:
            entry = self._store.get(key)
            return entry.result if entry else None

    async def put(self, key: str, result: RetrievalResult) -> None:
        async with self._lock:
            self._store[key] = _CacheEntry(result, time.monotonic())

    def clear(self) -> None:
        self._store.clear()


_cache = _TTLCache()


def _reset_cache_for_tests() -> None:
    _cache.clear()


# ── HTTP helpers ───────────────────────────────────────────────────────────────
def _round_coord(v: float, places: int = 3) -> float:
    """~100 m precision — enough for weather/facility caching, and it keeps the
    cache key stable across tiny GPS jitter so we don't re-hit APIs each turn."""
    return round(v, places)


async def _get_json(
    url: str, *, params: dict | None = None, timeout: float, retries: int = 1
) -> Any:
    """GET + parse JSON with a hard timeout and bounded retries.

    Raises on exhaustion; callers translate that into an honest failed result.
    """
    settings = get_settings()
    headers = {"User-Agent": settings.retrieval_user_agent, "Accept": "application/json"}
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
                resp = await client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:  # noqa: BLE001 - normalise all transport/parse errors
            last_exc = e
            if attempt < retries:
                await asyncio.sleep(0.4 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


# ── Geo helpers ────────────────────────────────────────────────────────────────
def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


# ── Weather (Open-Meteo, free, no key) ─────────────────────────────────────────
# WMO weather interpretation codes -> (text, hazard | None).
_WMO: dict[int, tuple[str, str | None]] = {
    0: ("clear sky", None),
    1: ("mainly clear", None),
    2: ("partly cloudy", None),
    3: ("overcast", None),
    45: ("fog", "reduced visibility"),
    48: ("freezing fog", "reduced visibility"),
    51: ("light drizzle", None),
    53: ("drizzle", None),
    55: ("heavy drizzle", None),
    61: ("light rain", None),
    63: ("rain", "heavy rain possible"),
    65: ("heavy rain", "flooding risk"),
    66: ("freezing rain", "ice hazard"),
    67: ("heavy freezing rain", "ice hazard"),
    71: ("light snow", None),
    73: ("snow", None),
    75: ("heavy snow", "heavy snow"),
    77: ("snow grains", None),
    80: ("rain showers", None),
    81: ("heavy rain showers", "heavy rain"),
    82: ("violent rain showers", "flooding risk"),
    85: ("snow showers", None),
    86: ("heavy snow showers", "heavy snow"),
    95: ("thunderstorm", "thunderstorm — seek shelter"),
    96: ("thunderstorm with hail", "severe thunderstorm — seek shelter"),
    99: ("thunderstorm with heavy hail", "severe thunderstorm — seek shelter"),
}


def _wmo(code: int | None) -> tuple[str, str | None]:
    if code is None:
        return ("unknown", None)
    return _WMO.get(int(code), (f"code {code}", None))


def _derive_weather_hazards(current: dict, daily: dict) -> list[str]:
    """Flag hazardous conditions from the forecast. These are DERIVED from a
    general forecast, not official meteorological warnings — the tool labels them
    as such so the agent never claims an official alert exists."""
    hz: list[str] = []
    _, chaz = _wmo(current.get("weather_code"))
    if chaz:
        hz.append(chaz)
    feels = current.get("apparent_temperature")
    if isinstance(feels, (int, float)):
        if feels >= 40:
            hz.append("extreme heat — risk of heat stroke")
        elif feels <= 0:
            hz.append("freezing conditions — risk of hypothermia")
    wind = current.get("wind_speed_10m")
    if isinstance(wind, (int, float)) and wind >= 50:
        hz.append("high winds")
    # Near-term: today/tomorrow heavy precipitation or wind.
    day_codes = (daily or {}).get("weather_code") or []
    for dc in day_codes[:2]:
        _, dh = _wmo(dc)
        if dh and dh not in hz:
            hz.append(f"forecast: {dh}")
    precip = (daily or {}).get("precipitation_sum") or []
    if precip and isinstance(precip[0], (int, float)) and precip[0] >= 50:
        hz.append("heavy rainfall expected today — localised flooding possible")
    # De-dupe preserving order.
    seen: set[str] = set()
    return [h for h in hz if not (h in seen or seen.add(h))]


async def get_weather(lat: float, lng: float) -> RetrievalResult:
    settings = get_settings()
    key = f"weather:{_round_coord(lat)}:{_round_coord(lng)}"
    cached = await _cache.get_fresh(key, settings.weather_cache_seconds)
    if cached is not None:
        return cached

    source = "Open-Meteo (api.open-meteo.com)"
    try:
        data = await asyncio.wait_for(
            _get_json(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": lat,
                    "longitude": lng,
                    "current": "temperature_2m,apparent_temperature,weather_code,"
                    "wind_speed_10m,precipitation,relative_humidity_2m",
                    "daily": "weather_code,temperature_2m_max,temperature_2m_min,"
                    "precipitation_sum,wind_speed_10m_max",
                    "timezone": "auto",
                    "forecast_days": 2,
                },
                timeout=settings.retrieval_timeout_seconds,
                retries=1,
            ),
            timeout=settings.retrieval_timeout_seconds + 2,
        )
        current = data.get("current") or {}
        daily = data.get("daily") or {}
        text, _ = _wmo(current.get("weather_code"))
        summary = {
            "conditions": text,
            "temperature_c": current.get("temperature_2m"),
            "feels_like_c": current.get("apparent_temperature"),
            "humidity_pct": current.get("relative_humidity_2m"),
            "wind_kmh": current.get("wind_speed_10m"),
            "precip_mm": current.get("precipitation"),
            "hazards": _derive_weather_hazards(current, daily),
            "hazards_note": "Derived from a general forecast, NOT an official "
            "meteorological warning.",
        }
        result = RetrievalResult(
            ok=True,
            source=source,
            retrieved_at=_now_iso(),
            data_as_of=current.get("time"),
            data=summary,
        )
        await _cache.put(key, result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[retrieval] weather failed: {type(e).__name__}: {e}")
        stale = await _cache.get_any(key)
        if stale is not None:
            stale.stale = True
            stale.note = "Live weather fetch failed; showing the last cached value."
            return stale
        return RetrievalResult(
            ok=False,
            source=source,
            retrieved_at=_now_iso(),
            error="weather_unavailable",
            note="Could not retrieve live weather right now.",
        )


# ── Nearby hospitals (OpenStreetMap Overpass, free, no key) ─────────────────────
def _parse_overpass_hospitals(
    payload: dict, lat: float, lng: float, max_results: int
) -> list[dict]:
    out: list[dict] = []
    for el in payload.get("elements", []):
        tags = el.get("tags") or {}
        name = tags.get("name")
        if not name:
            continue
        elat = el.get("lat") or (el.get("center") or {}).get("lat")
        elng = el.get("lon") or (el.get("center") or {}).get("lon")
        if elat is None or elng is None:
            continue
        dist = haversine_m(lat, lng, float(elat), float(elng))
        phone = tags.get("emergency:phone") or tags.get("phone") or tags.get("contact:phone")
        out.append(
            {
                "name": name,
                "distance_m": round(dist),
                "phone": phone,
                "emergency_dept": tags.get("emergency") == "yes"
                or tags.get("healthcare") == "hospital",
                "lat": round(float(elat), 5),
                "lng": round(float(elng), 5),
            }
        )
    out.sort(key=lambda h: h["distance_m"])
    return out[:max_results]


async def find_hospitals(
    lat: float, lng: float, *, radius_m: int = 6000, max_results: int = 5
) -> RetrievalResult:
    settings = get_settings()
    radius_m = max(1000, min(radius_m, 15000))
    max_results = max(1, min(max_results, 10))
    key = f"hospitals:{_round_coord(lat)}:{_round_coord(lng)}:{radius_m}"
    cached = await _cache.get_fresh(key, settings.hospitals_cache_seconds)
    if cached is not None:
        return cached

    source = "OpenStreetMap (Overpass API)"
    query = (
        f'[out:json][timeout:{int(settings.retrieval_timeout_seconds)}];'
        f'(node["amenity"="hospital"](around:{radius_m},{lat},{lng});'
        f'way["amenity"="hospital"](around:{radius_m},{lat},{lng});'
        f'node["amenity"="clinic"]["emergency"="yes"](around:{radius_m},{lat},{lng});'
        f');out center tags 40;'
    )
    headers = {"User-Agent": settings.retrieval_user_agent}
    last_err: str | None = None
    for base in settings.overpass_urls:
        try:
            async with httpx.AsyncClient(
                timeout=settings.retrieval_timeout_seconds, headers=headers
            ) as client:
                resp = await asyncio.wait_for(
                    client.post(base, data={"data": query}),
                    timeout=settings.retrieval_timeout_seconds + 2,
                )
            resp.raise_for_status()
            payload = resp.json()
            hospitals = _parse_overpass_hospitals(payload, lat, lng, max_results)
            result = RetrievalResult(
                ok=True,
                source=source,
                retrieved_at=_now_iso(),
                data={
                    "hospitals": hospitals,
                    "radius_m": radius_m,
                    "count": len(hospitals),
                },
                note="Locations from community map data. Call ahead — I cannot "
                "confirm a hospital is open or has capacity. For an ambulance, "
                "dial the emergency number.",
            )
            await _cache.put(key, result)
            return result
        except Exception as e:  # noqa: BLE001 - try the next mirror
            last_err = f"{type(e).__name__}"
            logger.warning(f"[retrieval] overpass {base} failed: {last_err}")
            continue

    stale = await _cache.get_any(key)
    if stale is not None:
        stale.stale = True
        stale.note = "Live hospital lookup failed; showing the last cached list."
        return stale
    return RetrievalResult(
        ok=False,
        source=source,
        retrieved_at=_now_iso(),
        error="hospitals_unavailable",
        note="Could not retrieve a live hospital list right now. Dial the "
        "emergency number — they will route the nearest ambulance.",
    )


# ── Official disaster advisories (config-driven; honest when unconfigured) ──────
async def get_advisories(lat: float, lng: float) -> RetrievalResult:
    """Fetch official advisories from a configured feed.

    There is no free, stable, credential-free public India-wide advisory API, so
    this is OFF by default. When `DISASTER_ADVISORY_URL` is set we fetch it and
    pass through its items (the feed is treated as authoritative). When unset we
    say so plainly rather than inventing alerts.
    """
    settings = get_settings()
    url = settings.disaster_advisory_url
    source = "configured advisory feed" if url else "no advisory feed configured"
    if not url:
        return RetrievalResult(
            ok=False,
            source=source,
            retrieved_at=_now_iso(),
            error="not_configured",
            note="No live disaster-advisory feed is configured. Check official "
            "sources directly (e.g. NDMA, IMD, and local authorities) — I cannot "
            "confirm current advisories.",
        )

    key = f"advisory:{_round_coord(lat, 2)}:{_round_coord(lng, 2)}"
    cached = await _cache.get_fresh(key, settings.advisory_cache_seconds)
    if cached is not None:
        return cached
    try:
        data = await asyncio.wait_for(
            _get_json(
                url,
                params={"lat": lat, "lng": lng},
                timeout=settings.retrieval_timeout_seconds,
                retries=1,
            ),
            timeout=settings.retrieval_timeout_seconds + 2,
        )
        result = RetrievalResult(
            ok=True,
            source=source,
            retrieved_at=_now_iso(),
            data=data,
            note="Advisories passed through from the configured official feed.",
        )
        await _cache.put(key, result)
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[retrieval] advisory feed failed: {type(e).__name__}: {e}")
        return RetrievalResult(
            ok=False,
            source=source,
            retrieved_at=_now_iso(),
            error="advisory_unavailable",
            note="The configured advisory feed did not respond. Check official "
            "sources directly; I cannot confirm current advisories.",
        )
