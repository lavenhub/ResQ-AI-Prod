"""Builds + runs the Pipecat pipeline for ONE ambulance-coordination call.

The SAME Gemini Multimodal Live service + ambulance prompt + tools are reused
across two transports:
  * TELEPHONY  — FastAPIWebsocketTransport driven by a provider FrameSerializer
    (Twilio/Exotel): a real PSTN call.  -> run_ambulance_call()
  * WEBRTC     — SmallWebRTCTransport: a FREE in-browser "reception" call where a
    teammate answers from their browser (no carrier, no cost).  -> run_ambulance_call_webrtc()

Only the transport differs; the LLM, VAD, tools, kickoff turn and hang-up wiring
are identical, assembled once in ``_assemble_task``.
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
from pipecat.processors.frameworks.rtvi import RTVIObserverParams, RTVIProcessor
from pipecat.services.google.gemini_live.llm import GeminiLiveLLMService, GeminiModalities

from ..config import get_settings
from .ambulance_prompts import build_ambulance_system_instruction
from .ambulance_tools import AmbulanceCallTools, AmbulanceToolContext, build_ambulance_tools_schema
from .languages import resolve_language


def _assemble_task(
    transport,
    *,
    booking_id: str,
    is_demo: bool,
    on_connected: Callable[[], Awaitable[None]] | None = None,
) -> PipelineTask:
    """Build the LLM + tools + pipeline + task for one call over any transport.

    Wires: the ambulance system instruction, the three ambulance tools, a kickoff
    user turn so the AI opens the call the instant the far end connects, an
    end-call hook for the end_call_with_summary tool, and a single greet-on-connect
    handler (which also fires the optional ``on_connected`` callback). Returns the
    ready-to-run PipelineTask.
    """
    settings = get_settings()
    lang_enum, _ = resolve_language("English")  # the receptionist side is English

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

    # Kickoff turn => the model OPENS the call (opening wording comes from the
    # system instruction). Without it Gemini Live waits silently for the far end.
    context = LLMContext(
        messages=[{
            "role": "user",
            "content": "The ambulance reception desk just answered. Open the call now with "
            "your short greeting that discloses you are an AI calling for ResQAI, then wait "
            "for their reply.",
        }]
    )
    context_aggregator = LLMContextAggregatorPair(context)

    # RTVI streams speaking-state + transcription over the `chat` data channel so
    # the WebRTC reception page can show a live transcript. Unused by the
    # telephony (Twilio) path, which has no browser data channel — harmless there.
    rtvi = RTVIProcessor()

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
        rtvi_processor=rtvi,
        rtvi_observer_params=RTVIObserverParams(
            bot_speaking_enabled=True,
            user_speaking_enabled=True,
            user_transcription_enabled=True,
            bot_tts_enabled=True,
            bot_llm_enabled=True,
        ),
        idle_timeout_secs=settings.ambulance_max_call_seconds,
    )

    async def _end_call() -> None:
        logger.info(f"[ambulance-pipeline] agent requested end of call ({booking_id})")
        await task.queue_frames([EndFrame()])

    tool_ctx.end_call = _end_call

    @transport.event_handler("on_client_connected")
    async def _greet_on_connect(_transport, _client) -> None:  # noqa: ANN001
        logger.info(f"[ambulance-pipeline] far end connected ({booking_id}) — opening the call")
        if on_connected is not None:
            await on_connected()
        await task.queue_frames([LLMRunFrame()])

    return task


async def run_ambulance_call(
    websocket,
    serializer,
    *,
    booking_id: str,
    is_demo: bool = False,
    on_finished: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one outbound ambulance call over a TELEPHONY media WebSocket (PSTN)."""
    from pipecat.transports.websocket.fastapi import (
        FastAPIWebsocketParams,
        FastAPIWebsocketTransport,
    )

    settings = get_settings()
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
    task = _assemble_task(transport, booking_id=booking_id, is_demo=is_demo)

    @transport.event_handler("on_session_timeout")
    async def _on_timeout(_transport, _ws) -> None:  # noqa: ANN001
        logger.info(f"[ambulance-pipeline] session timeout ({booking_id})")
        await task.queue_frames([EndFrame()])

    await _run(task, booking_id, on_finished)


async def run_ambulance_call_webrtc(
    connection,
    *,
    booking_id: str,
    is_demo: bool = False,
    on_connected: Callable[[], Awaitable[None]] | None = None,
    on_finished: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Run one ambulance call over a FREE in-browser WebRTC reception connection."""
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    settings = get_settings()
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.6)),
        ),
    )
    task = _assemble_task(
        transport, booking_id=booking_id, is_demo=is_demo, on_connected=on_connected
    )

    @connection.event_handler("closed")
    async def _on_closed(_conn) -> None:  # noqa: ANN001
        logger.info(f"[ambulance-pipeline] reception connection closed ({booking_id})")
        await task.cancel()

    await _run(task, booking_id, on_finished)


async def _run(
    task: PipelineTask, booking_id: str, on_finished: Callable[[], Awaitable[None]] | None
) -> None:
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
