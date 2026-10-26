"""Tools for the OUTBOUND ambulance-coordination caller.

These are the ONLY actions the caller LLM can take. Each is schema-constrained,
validated server-side, scoped to THIS booking (the model can't address another),
and routed through ``ambulance_service`` so the backend — not the model — owns
the state machine. The model reports what the receptionist SAID; it cannot set a
booking to confirmed/dispatched on its own.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from loguru import logger

from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.services.llm_service import FunctionCallParams

from .. import ambulance_service
from ..db import session_scope
from ..models import BookingState, TERMINAL_BOOKING_STATES


@dataclass
class AmbulanceToolContext:
    booking_id: str
    is_demo: bool = False
    # Set by the pipeline so a tool can end the call (queues an EndFrame).
    end_call: Callable[[], Awaitable[None]] | None = field(default=None)


class AmbulanceCallTools:
    def __init__(self, ctx: AmbulanceToolContext) -> None:
        self.ctx = ctx

    # ── tool: get verified booking details ───────────────────────────────────
    async def get_booking_details(self, params: FunctionCallParams) -> None:
        booking_id = self.ctx.booking_id

        def _work() -> dict | None:
            with session_scope() as s:
                booking = ambulance_service.get_booking(s, booking_id)
                if booking is None:
                    return None
                return ambulance_service.booking_context(s, booking)

        ctx = await asyncio.to_thread(_work)
        if ctx is None:
            await params.result_callback({"ok": False, "error": "booking_not_found"})
            return
        await params.result_callback({
            "ok": True,
            "details": ctx,
            "note": "Share only these verified details. Anything null is unknown — "
            "say it is not available rather than guessing.",
        })

    # ── tool: record what the receptionist said ──────────────────────────────
    async def record_receptionist_response(self, params: FunctionCallParams) -> None:
        args = params.arguments or {}
        booking_id = self.ctx.booking_id

        def _work() -> dict | None:
            with session_scope() as s:
                booking = ambulance_service.get_booking(s, booking_id)
                if booking is None:
                    return None
                booking = ambulance_service.record_receptionist_response(
                    s, booking,
                    availability=args.get("availability"),
                    ambulance_type=args.get("ambulance_type"),
                    eta=args.get("eta"),
                    charges=args.get("charges"),
                    booking_reference=args.get("booking_reference"),
                    booking_accepted=args.get("booking_accepted"),
                    explicit_confirmation=args.get("explicit_confirmation"),
                    declined=args.get("declined"),
                    situation_code=args.get("situation_code"),
                    needs_more_info=args.get("needs_more_info"),
                    transferred=args.get("transferred"),
                    notes=args.get("notes"),
                )
                # Queue authorized email updates on a decisive outcome.
                if booking.booking_state == BookingState.BOOKING_CONFIRMED:
                    ambulance_service.enqueue_booking_update(s, booking, "confirmed")
                elif booking.booking_state == BookingState.BOOKING_DECLINED:
                    ambulance_service.enqueue_booking_update(s, booking, "declined")
                return {
                    "state": booking.booking_state.value,
                    "availability": booking.availability.value,
                    "accepted": booking.booking_accepted,
                }

        result = await asyncio.to_thread(_work)
        if result is None:
            await params.result_callback({"ok": False, "error": "booking_not_found"})
            return
        logger.info(f"[ambulance-tools] recorded response -> {result}")
        # Honest feedback to the model: tell it the BACKEND-recorded state, and
        # that only the backend confirms a booking.
        await params.result_callback({
            "ok": True,
            "recorded_state": result["state"],
            "availability": result["availability"],
            "booking_accepted": result["accepted"],
            "note": "Recorded. The booking is only 'confirmed' once the backend has "
            "validated an explicit confirmation — reporting it does not make it so.",
        })

    # ── tool: end the call with a summary ────────────────────────────────────
    async def end_call_with_summary(self, params: FunctionCallParams) -> None:
        args = params.arguments or {}
        outcome = str(args.get("outcome", ""))[:1000]
        booking_id = self.ctx.booking_id

        def _work() -> str:
            with session_scope() as s:
                booking = ambulance_service.get_booking(s, booking_id)
                if booking is None:
                    return "booking_not_found"
                if outcome and not booking.receptionist_response:
                    booking.receptionist_response = outcome
                # If the call reached no decisive outcome, mark it for follow-up.
                if booking.booking_state not in TERMINAL_BOOKING_STATES:
                    if ambulance_service.can_transition(
                        booking.booking_state, BookingState.PENDING_FOLLOW_UP
                    ):
                        ambulance_service.transition(
                            s, booking, BookingState.PENDING_FOLLOW_UP,
                            detail="call ended by agent without a confirmed outcome",
                        )
                    else:
                        ambulance_service.transition(
                            s, booking, BookingState.COMPLETED,
                            detail="call ended by agent", force=True,
                        )
                s.add(booking)
                s.commit()
                return booking.booking_state.value

        state = await asyncio.to_thread(_work)
        await params.result_callback({"ok": True, "final_state": state})
        # Hang up the call (queues an EndFrame via the pipeline).
        if self.ctx.end_call is not None:
            await self.ctx.end_call()

    def register(self, llm) -> None:
        llm.register_function("get_booking_details", self.get_booking_details)
        llm.register_function("record_receptionist_response", self.record_receptionist_response)
        llm.register_function("end_call_with_summary", self.end_call_with_summary)


def build_ambulance_tools_schema() -> ToolsSchema:
    return ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="get_booking_details",
                description="Get the VERIFIED patient and pickup-location details for this "
                "call. Call this before sharing any details. Anything null is unknown — "
                "never invent it.",
                properties={},
                required=[],
            ),
            FunctionSchema(
                name="record_receptionist_response",
                description="Record what the receptionist ACTUALLY said. Only include fields "
                "they actually gave you; leave the rest blank. You do not set the booking "
                "status — the backend does — so just report faithfully.",
                properties={
                    "availability": {
                        "type": "string",
                        "enum": ["AVAILABLE", "UNAVAILABLE", "UNKNOWN"],
                        "description": "Whether an ambulance is available, per the receptionist.",
                    },
                    "ambulance_type": {"type": "string", "description": "Type offered (basic/ICU/cardiac…), if stated."},
                    "eta": {"type": "string", "description": "Estimated arrival time, ONLY if actually given."},
                    "charges": {"type": "string", "description": "Charges/estimate, ONLY if actually given."},
                    "booking_reference": {"type": "string", "description": "Booking/reference number, if provided."},
                    "booking_accepted": {"type": "boolean", "description": "True only if they clearly agree to arrange one."},
                    "explicit_confirmation": {"type": "boolean", "description": "True ONLY after they explicitly confirm the booking (not mere availability)."},
                    "declined": {"type": "boolean", "description": "True if they refuse or cannot help."},
                    "needs_more_info": {"type": "boolean", "description": "True if they ask for more patient/location details."},
                    "transferred": {"type": "boolean", "description": "True if the call was handed to someone else."},
                    "situation_code": {
                        "type": "string",
                        "enum": sorted({"A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N"}),
                        "description": "Single best-matching situation letter (see your instructions).",
                    },
                    "notes": {"type": "string", "description": "Short faithful paraphrase of their key reply."},
                },
                required=[],
            ),
            FunctionSchema(
                name="end_call_with_summary",
                description="End the call once an outcome is reached. Give your one-line "
                "spoken summary first, then call this with a brief outcome description.",
                properties={
                    "outcome": {"type": "string", "description": "Brief description of how the call ended."},
                },
                required=["outcome"],
            ),
        ]
    )
