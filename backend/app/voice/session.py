"""Tracks active voice sessions for concurrency limits and clean teardown.

A hackathon free tier can be exhausted (or billed) by too many concurrent
real-time sessions, so we cap them and always release the slot on close.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class SessionManager:
    max_concurrent: int
    _active: dict[str, object] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def try_acquire(self, session_id: str, handle: object = None) -> bool:
        async with self._lock:
            if session_id in self._active:
                return True  # renegotiation of an existing session
            if len(self._active) >= self.max_concurrent:
                logger.warning(
                    f"[session] rejecting {session_id}: at capacity "
                    f"({len(self._active)}/{self.max_concurrent})"
                )
                return False
            self._active[session_id] = handle
            logger.info(f"[session] acquired {session_id} ({len(self._active)}/{self.max_concurrent})")
            return True

    async def release(self, session_id: str) -> None:
        async with self._lock:
            if self._active.pop(session_id, None) is not None:
                logger.info(
                    f"[session] released {session_id} ({len(self._active)}/{self.max_concurrent})"
                )

    @property
    def active_count(self) -> int:
        return len(self._active)
