"""Shared ICE-server configuration for every WebRTC path (dispatcher + reception).

STUN alone suffices on a local network. On a cloud host whose network blocks
WebRTC's UDP, a TURN relay is required or media never flows (the call "connects"
but is silent). TURN is opt-in via env (TURN_URLS / TURN_USERNAME /
TURN_CREDENTIAL); the same config is served to the browser (``ice_servers_client``)
and used by the server's aiortc peer (``ice_servers_aiortc``) so both ends agree.
"""

from __future__ import annotations

from .config import get_settings


def ice_servers_client() -> list[dict]:
    """ICE servers in the browser RTCConfiguration shape (served via /api/ice)."""
    s = get_settings()
    servers: list[dict] = []
    if s.stun_urls:
        servers.append({"urls": list(s.stun_urls)})
    if s.turn_urls:
        entry: dict = {"urls": list(s.turn_urls)}
        if s.turn_username:
            entry["username"] = s.turn_username
        if s.turn_credential:
            entry["credential"] = s.turn_credential
        servers.append(entry)
    return servers


def ice_servers_aiortc() -> list:
    """The same ICE servers as pipecat/aiortc IceServer objects for the server peer."""
    from pipecat.transports.smallwebrtc.connection import IceServer

    s = get_settings()
    servers: list = []
    for url in s.stun_urls:
        servers.append(IceServer(urls=url))
    if s.turn_urls:
        servers.append(
            IceServer(
                urls=list(s.turn_urls),
                username=s.turn_username or None,
                credential=s.turn_credential or None,
            )
        )
    return servers or [IceServer(urls="stun:stun.l.google.com:19302")]
