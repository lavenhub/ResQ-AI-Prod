"""Builds and runs the Pipecat voice pipeline for a single WebRTC connection.

    mic ─▶ SmallWebRTC in ─▶ RTVI ─▶ ctx.user ─▶ Gemini Live ─▶ SmallWebRTC out ─▶ speaker
                                                      │
                                                      └─▶ tools (create/update incident, location)

Gemini Multimodal Live does STT + reasoning + TTS natively (low latency, barge-in,
native Hindi/Marathi), so there is no separate STT/TTS stage. The RTVI processor
emits speaking-state + transcription messages over the `chat` data channel, which
the existing frontend already consumes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.frameworks.rtvi import RTVIObserverParams, RTVIProcessor
from pipecat.services.google.gemini_live.llm import (
    GeminiLiveLLMService,
    GeminiModalities,
)
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

from ..config import get_settings
from .languages import resolve_language
from .prompts import build_system_instruction
from .tools import VoiceTools, VoiceToolContext, build_tools_schema


def build_voice_task(
    connection: SmallWebRTCConnection,
    *,
    session_id: str,
    language_name: str,
    user_ref: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    location_accuracy: float | None = None,
    address: str | None = None,
) -> PipelineTask:
    """Assemble the pipeline + task for one session (no network until run)."""
    settings = get_settings()
    lang_enum, native = resolve_language(language_name)
    if not native:
        logger.warning(
            f"[pipeline] language '{language_name}' not natively supported; "
            f"falling back to {lang_enum.value}"
        )

    location_hint = address or (
        f"{latitude:.5f}, {longitude:.5f}" if latitude is not None and longitude is not None else None
    )
    system_instruction = build_system_instruction(language_name, location_hint=location_hint)

    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.5)),
        ),
    )

    tools_schema = build_tools_schema()
    gemini_settings_kwargs: dict = dict(
        voice=settings.gemini_voice,
        language=lang_enum,
        modalities=GeminiModalities.AUDIO,
        system_instruction=system_instruction,
    )
    if settings.gemini_live_model:  # else use the service's current default model
        gemini_settings_kwargs["model"] = settings.gemini_live_model
    llm = GeminiLiveLLMService(
        api_key=settings.gemini_api_key,
        tools=tools_schema,
        settings=GeminiLiveLLMService.Settings(**gemini_settings_kwargs),
    )

    tool_ctx = VoiceToolContext(
        session_id=session_id,
        user_ref=user_ref,
        detected_language=lang_enum.value,
        latitude=latitude,
        longitude=longitude,
        location_accuracy=location_accuracy,
        address=address,
    )
    VoiceTools(tool_ctx).register(llm)

    # Seed ONE kickoff user turn so the realtime model has something to respond to
    # the instant the session is ready — this is what makes the dispatcher SPEAK
    # FIRST. With an empty context, Gemini Live just waits silently for the caller
    # to talk (which reads as "the call is dead"). The actual opening wording comes
    # from the system instruction (prompts.py "OPENING THE CALL"); this turn is only
    # the trigger and is never shown to the caller.
    context = LLMContext(
        messages=[
            {
                "role": "user",
                "content": "The emergency call just connected. Open the call now with your "
                "opening line, then wait for me to respond.",
            }
        ]
    )
    context_aggregator = LLMContextAggregatorPair(context)

    # RTVI carries speaking-state + transcription messages to the frontend over
    # the `chat` data channel. Passing the processor + observer params to
    # PipelineTask lets the framework insert both in the correct position.
    # (Audio streaming is driven by frames, so RTVIProcessor needs no transport.)
    rtvi = RTVIProcessor()

    pipeline = Pipeline(
        [
            transport.input(),
            context_aggregator.user(),
            llm,
            transport.output(),
            context_aggregator.assistant(),
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        rtvi_processor=rtvi,
        rtvi_observer_params=RTVIObserverParams(
            bot_speaking_enabled=True,
            user_speaking_enabled=True,
            user_transcription_enabled=True,
            bot_tts_enabled=True,
            bot_llm_enabled=True,
        ),
        # Hard cap on session length (cost/abuse guardrail).
        idle_timeout_secs=settings.max_session_seconds,
    )

    # Speak first: the instant the caller's WebRTC connection is up, prompt the
    # LLM to run so the dispatcher opens with a greeting. Without this, Gemini
    # Live waits silently for the caller to talk — which reads as "nothing
    # happened" and was a likely cause of the call appearing dead. The opening
    # line itself is defined by the system instruction (see prompts.py).
    @transport.event_handler("on_client_connected")
    async def _greet_on_connect(_transport, _client) -> None:  # noqa: ANN001
        logger.info(f"[pipeline] client connected ({session_id}) — sending opening greeting")
        await task.queue_frames([LLMRunFrame()])

    return task


async def run_voice_bot(
    connection: SmallWebRTCConnection,
    *,
    session_id: str,
    language_name: str,
    user_ref: str | None,
    latitude: float | None,
    longitude: float | None,
    location_accuracy: float | None,
    address: str | None,
    on_finished: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one voice session to completion. Intended to be launched as a task."""
    task = build_voice_task(
        connection,
        session_id=session_id,
        language_name=language_name,
        user_ref=user_ref,
        latitude=latitude,
        longitude=longitude,
        location_accuracy=location_accuracy,
        address=address,
    )

    @connection.event_handler("closed")
    async def _on_closed(_conn):  # noqa: ANN001
        logger.info(f"[pipeline] connection closed: {session_id}")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=False)
    try:
        logger.info(f"[pipeline] starting session {session_id} (lang={language_name})")
        await runner.run(task)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[pipeline] session {session_id} error: {e}")
    finally:
        logger.info(f"[pipeline] session {session_id} finished")
        if on_finished is not None:
            await on_finished()
