"""Builds + runs the Pipecat pipeline for ONE outbound ambulance call.

    reception phone ─▶ provider Media Stream (ws) ─▶ serializer ─▶ ctx.user
        ─▶ Gemini Live ─▶ serializer ─▶ provider Media Stream ─▶ reception phone
                                 │
                                 └─▶ ambulance tools (record response, end call)

This is the SAME Gemini Multimodal Live service the browser dispatcher uses
(voice/pipeline.py) — only the transport changes: a ``FastAPIWebsocketTransport``
driven by a telephony ``FrameSerializer`` (Twilio/Exotel) instead of SmallWebRTC.
The dispatcher pipeline is left untouched; this reuses the LLM, VAD and tool
machinery for the outbound call.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from loguru import logger

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import EndFrame, LLMRunFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService, GeminiModalities
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from ..config import get_settings
from .ambulance_prompts import build_ambulance_system_instruction
from .ambulance_tools import AmbulanceCallTools, AmbulanceToolContext, build_ambulance_tools_schema
from .languages import resolve_language


async def run_ambulance_call(
    websocket,
    serializer: FrameSerializer,
    *,
    booking_id: str,
    is_demo: bool = False,
    on_finished: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one outbound ambulance call to completion over a telephony media WS."""
    settings = get_settings()
    lang_enum, _ = resolve_language("English")  # receptionist side is English

    transport = FastAPIWebsocketTransport(
        websocket=websocket,
        params=FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            add_wav_header=False,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6)),
            serializer=serializer,
            session_timeout=settings.ambulance_max_call_seconds,
        ),
    )

    system_instruction = build_ambulance_system_instruction(is_demo=is_demo)
    gemini_kwargs: dict = dict(
        voice=settings.ambulance_voice,
        language=lang_enum,
        modalities=GeminiModalities.AUDIO,
        system_instruction=system_instruction,
    )
    if settings.gemini_live_model:
        gemini_kwargs["model"] = settings.gemini_live_model
    llm = GeminiLiveLLMService(
        api_key=settings.gemini_api_key,
        tools=build_ambulance_tools_schema(),
        settings=GeminiLiveLLMService.Settings(**gemini_kwargs),
    )

    tool_ctx = AmbulanceToolContext(booking_id=booking_id, is_demo=is_demo)
    AmbulanceCallTools(tool_ctx).register(llm)

    # Seed ONE kickoff user turn so the realtime model OPENS the call the instant
    # the receptionist answers (the opening wording comes from the system
    # instruction). Without this, Gemini Live waits silently for the far end.
    context = LLMContext(
        messages=[{
            "role": "user",
            "content": "The ambulance reception desk just answered the phone. Open the "
            "call now with your short greeting that discloses you are an AI calling for "
            "ResQAI, then wait for their reply.",
        }]
    )
    context_aggregator = LLMContextAggregatorPair(context)

    pipeline = Pipeline([
        transport.input(),
        context_aggregator.user(),
        llm,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(enable_metrics=True, enable_usage_metrics=True),
        idle_timeout_secs=settings.ambulance_max_call_seconds,
    )

    # Let the end_call_with_summary tool hang up the call cleanly.
    async def _end_call() -> None:
        logger.info(f"[ambulance-pipeline] agent requested end of call ({booking_id})")
        await task.queue_frames([EndFrame()])

    tool_ctx.end_call = _end_call

    @transport.event_handler("on_client_connected")
    async def _greet_on_connect(_transport, _client) -> None:  # noqa: ANN001
        logger.info(f"[ambulance-pipeline] media connected ({booking_id}) — opening the call")
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_session_timeout")
    async def _on_timeout(_transport, _ws) -> None:  # noqa: ANN001
        logger.info(f"[ambulance-pipeline] session timeout ({booking_id})")
        await task.queue_frames([EndFrame()])

    runner = PipelineRunner(handle_sigint=False)
    try:
        logger.info(f"[ambulance-pipeline] starting call pipeline for booking {booking_id}")
        await runner.run(task)
    except Exception as e:  # noqa: BLE001
        logger.exception(f"[ambulance-pipeline] call {booking_id} error: {e}")
    finally:
        logger.info(f"[ambulance-pipeline] call pipeline finished for booking {booking_id}")
        if on_finished is not None:
            await on_finished()
