"""Shared ICE-server configuration for every WebRTC path (dispatcher + reception).

STUN alone suffices on a local network. On a cloud host whose network blocks
WebRTC's UDP (e.g. Render), a TURN relay is required or media never flows.

Three ways to supply TURN, in priority order:
  1. Cloudflare TURN (recommended): set CLOUDFLARE_TURN_TOKEN_ID +
     CLOUDFLARE_TURN_API_TOKEN and the backend MINTS short-lived credentials on
     demand (cached), for both the browser and the server's aiortc peer — nothing
     to copy or expire.
  2. Static TURN: set TURN_URLS / TURN_USERNAME / TURN_CREDENTIAL.
  3. None: STUN only (fine locally).

The same resolved servers are served to the browser (``ice_servers_client``) and
used by the server peer (``ice_servers_aiortc``) so both ends agree. All
functions are async because minting may do a network call.
"""

from __future__ import annotations

import asyncio
import time

import httpx
from loguru import logger

from .config import get_settings

_CF_TTL = 86400  # request 24h credentials from Cloudflare
_CF_API = "https://rtc.live.cloudflare.com/v1/turn/keys/{key}/credentials/generate"

# Cache of the last minted Cloudflare TURN block: (payload, expires_at_epoch).
_cf_cache: tuple[dict, float] | None = None
_cf_lock = asyncio.Lock()


async def _cloudflare_turn() -> dict | None:
    """Return a minted Cloudflare TURN block ``{urls, username, credential}`` or
    None if not configured / the mint failed. Cached until shortly before expiry."""
    global _cf_cache
    s = get_settings()
    if not (s.cloudflare_turn_token_id and s.cloudflare_turn_api_token):
        return None

    now = time.time()
    if _cf_cache and _cf_cache[1] > now:
        return _cf_cache[0]

    async with _cf_lock:
        if _cf_cache and _cf_cache[1] > time.time():  # recheck after acquiring lock
            return _cf_cache[0]
        url = _CF_API.format(key=s.cloudflare_turn_token_id)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {s.cloudflare_turn_api_token}"},
                    json={"ttl": _CF_TTL},
                )
        except httpx.HTTPError as exc:
            logger.warning(f"[ice] Cloudflare TURN mint network error: {exc}")
            return None
        if resp.status_code >= 400:
            logger.warning(
                f"[ice] Cloudflare TURN mint failed {resp.status_code}: {resp.text[:200]}"
            )
            return None
        ice = (resp.json() or {}).get("iceServers") or {}
        urls = ice.get("urls")
        if isinstance(urls, str):
            urls = [urls]
        if not urls or not ice.get("username") or not ice.get("credential"):
            logger.warning("[ice] Cloudflare TURN mint returned an unexpected payload")
            return None
        # Keep only turn/turns urls here (STUN is added separately).
        turn_urls = [u for u in urls if u.startswith(("turn:", "turns:"))] or urls
        block = {
            "urls": turn_urls,
            "username": ice["username"],
            "credential": ice["credential"],
        }
        _cf_cache = (block, now + _CF_TTL - 300)  # refresh 5 min before expiry
        logger.info("[ice] minted Cloudflare TURN credentials")
        return block


async def _turn_block() -> dict | None:
    """Resolve the active TURN block: Cloudflare (minted) else static env else None."""
    cf = await _cloudflare_turn()
    if cf:
        return cf
    s = get_settings()
    if s.turn_urls:
        block: dict = {"urls": list(s.turn_urls)}
        if s.turn_username:
            block["username"] = s.turn_username
        if s.turn_credential:
            block["credential"] = s.turn_credential
        return block
    return None


async def ice_servers_client() -> list[dict]:
    """ICE servers in the browser RTCConfiguration shape (served via /api/ice)."""
    s = get_settings()
    servers: list[dict] = []
    if s.stun_urls:
        servers.append({"urls": list(s.stun_urls)})
    turn = await _turn_block()
    if turn:
        servers.append(turn)
    return servers


def _server_turn_order(urls: list[str]) -> list[str]:
    """aiortc uses only the FIRST valid TURN url (see rtcicetransport.
    connection_kwargs). Many cloud hosts (e.g. Render) block outbound UDP, so the
    UDP relay never allocates. Order TLS/TCP first so the SERVER peer picks a
    TCP-based relay that works through outbound-TCP-only egress. (Browsers get the
    natural order via ice_servers_client and still try UDP first.)"""
    def rank(u: str) -> int:
        if u.startswith("turns:"):
            return 0                 # TLS over TCP — most egress-friendly
        if "transport=tcp" in u:
            return 1                 # plain TCP
        return 2                     # UDP last
    return sorted(urls, key=rank)


async def ice_servers_aiortc() -> list:
    """The same ICE servers as pipecat/aiortc IceServer objects for the server peer."""
    from pipecat.transports.smallwebrtc.connection import IceServer

    s = get_settings()
    servers: list = [IceServer(urls=u) for u in s.stun_urls]
    turn = await _turn_block()
    if turn:
        servers.append(
            IceServer(
                urls=_server_turn_order(turn["urls"]),
                username=turn.get("username") or None,
                credential=turn.get("credential") or None,
            )
        )
    return servers or [IceServer(urls="stun:stun.l.google.com:19302")]
