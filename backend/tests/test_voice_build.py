"""Voice pipeline wiring test — constructs the Pipecat/Gemini pipeline with a
dummy key (no network). Guards against API drift in the voice layer.

Does NOT verify a live call (that needs a real key + a microphone); it only
proves every constructor + the RTVI/tool wiring is correct.
"""

from __future__ import annotations

from app.config import get_settings


def test_build_voice_task_constructs(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "dummy-key-not-used-for-network")
    get_settings.cache_clear()
    try:
        from pipecat.pipeline.task import PipelineTask
        from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection

        from app.voice.pipeline import build_voice_task

        assert get_settings().voice_enabled is True

        for lang in ("English", "Marathi"):  # covers EN + a non-English enum
            conn = SmallWebRTCConnection(ice_servers=["stun:stun.l.google.com:19302"])
            task = build_voice_task(
                conn,
                session_id=f"wiring-{lang}",
                language_name=lang,
                latitude=19.07,
                longitude=72.87,
                address="Mumbai",
            )
            assert isinstance(task, PipelineTask)
    finally:
        get_settings.cache_clear()
