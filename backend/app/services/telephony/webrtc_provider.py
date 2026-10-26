"""WebRTC "reception" provider — a FREE alternative to PSTN telephony.

No carrier, no account, no cost: instead of dialing a phone number, a human
"receptionist" (a teammate) joins the call from their BROWSER over WebRTC, and
the same AI ambulance-coordinator pipeline talks to them live. This is the exact
SmallWebRTC transport the in-app dispatcher already uses, so it's a real
two-way real-time AI voice conversation — just not over the phone network.

It satisfies the TelephonyProvider interface so the booking flow + state machine
are identical to the PSTN path:
  * ``initiate_outbound_call`` does NOT dial — it marks the booking as RINGING
    (waiting for the receptionist to join) and returns the booking id as the
    "call id". The actual audio connection is established when the receptionist
    opens the reception page and sends a WebRTC offer (routes/ambulance_reception).
  * webhooks / TwiML / media-serializer are not used (SmallWebRTC needs none).
"""

from __future__ import annotations

from ...config import get_settings
from ...models import CallStatus
from .base import CallRequest, CallResult, CallStatusUpdate, TelephonyProvider


class WebRTCTelephonyProvider(TelephonyProvider):
    name = "webrtc"

    async def initiate_outbound_call(self, req: CallRequest) -> CallResult:
        if problems := self.validate_configuration():
            return CallResult(
                ok=False, provider=self.name, status=CallStatus.FAILED,
                detail="WebRTC reception not ready: " + "; ".join(problems),
            )
        # No PSTN dial. The receptionist joins from their browser; until then the
        # booking simply "rings". The booking id is the join capability.
        return CallResult(
            ok=True,
            provider=self.name,
            provider_call_id=req.booking_id,
            status=CallStatus.RINGING,
            detail="waiting for the receptionist to join from their browser",
        )

    async def get_call_status(self, provider_call_id: str) -> CallStatusUpdate | None:
        # Status is driven by the WebRTC pipeline lifecycle, not polled here.
        return None

    async def terminate_call(self, provider_call_id: str) -> bool:
        # The reception route closes the peer connection; nothing to call out to.
        return True

    def verify_webhook(self, *, url, raw_body, form, headers) -> bool:  # noqa: ANN001
        # No provider webhooks in WebRTC mode.
        return False

    def parse_status_webhook(self, form):  # noqa: ANN001
        return None

    def build_call_answer_document(self, *, media_ws_url: str, booking_id: str):
        raise NotImplementedError("WebRTC reception does not use an answer document")

    def build_media_serializer(self, start_payload: dict):
        raise NotImplementedError("WebRTC reception does not use a media serializer")

    def validate_configuration(self) -> list[str]:
        problems: list[str] = []
        if not get_settings().gemini_api_key:
            problems.append("GEMINI_API_KEY is required for the WebRTC reception call")
        return problems
