"""Dev check: construct the voice pipeline for several languages (no network).

    cd backend && PYTHONPATH=. .venv/Scripts/python scripts/check_pipeline.py

Uses a dummy GEMINI_API_KEY so the Gemini Live service + transport + tools +
RTVI all instantiate without connecting anywhere. A live call still requires a
real key and a microphone.
"""

from __future__ import annotations

import os

os.environ.setdefault("GEMINI_API_KEY", "dummy-key-for-construction-check")
os.environ.setdefault("DATABASE_URL", "sqlite:///./construct_check.db")

from pipecat.pipeline.task import PipelineTask  # noqa: E402
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.voice.pipeline import build_voice_task  # noqa: E402


def main() -> None:
    print("voice_enabled:", get_settings().voice_enabled)
    for lang in ["English", "Hindi", "Marathi", "Konkani"]:  # Konkani -> fallback
        conn = SmallWebRTCConnection(ice_servers=["stun:stun.l.google.com:19302"])
        task = build_voice_task(
            conn, session_id=f"build-{lang}", language_name=lang,
            latitude=19.07, longitude=72.87, address="Mumbai",
        )
        assert isinstance(task, PipelineTask), lang
        print(f"  [OK] built PipelineTask for {lang}")
    print("ALL PIPELINE CONSTRUCTION CHECKS PASSED")


if __name__ == "__main__":
    main()
