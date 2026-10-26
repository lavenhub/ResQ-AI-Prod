"""The telephony provider interface + shared value types.

The ambulance-calling logic depends ONLY on this interface, so another provider
(Exotel today, Plivo/Telnyx later) can be added without touching the pipeline or
the state machine. The interface separates the concerns the mission calls out:

  * initiate_outbound_call      — place one real outbound PSTN call
  * get_call_status             — poll the provider for a call's status
  * terminate_call              — hang up a live call
  * validate_configuration      — fail fast with actionable errors (no network)
  * verify_webhook              — authenticate an inbound provider webhook
  * parse_status_webhook        — map a provider status callback to our CallStatus
  * build_call_answer_document  — the per-call instruction doc the provider fetches
                                  on answer (e.g. Twilio TwiML connecting a media
                                  stream to our AI WebSocket)
  * build_media_serializer      — the Pipecat FrameSerializer for the media stream

Nothing here imports a concrete provider SDK, and the heavy Pipecat media
serializers are imported lazily inside the concrete providers, so importing this
package (and the mock) stays cheap and SDK-free for tests.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field

from ...models import CallStatus


@dataclass(frozen=True)
class CallRequest:
    """Everything a provider needs to place one outbound call."""

    to_number: str                 # E.164 destination (validated + allowlisted upstream)
    from_number: str               # caller ID (a provider-owned / verified number)
    booking_id: str                # our reference, echoed back on webhooks
    # The public HTTPS URL the provider fetches on answer to get call instructions
    # (Twilio: TwiML). The provider may instead be handed inline instructions.
    answer_url: str
    # The public HTTPS URL the provider POSTs call-status callbacks to.
    status_callback_url: str
    # The public wss:// URL the media stream connects to (used to build the
    # answer document).
    media_ws_url: str
    # Hard cap on call length (seconds).
    max_seconds: int = 300


@dataclass(frozen=True)
class CallResult:
    """Outcome of asking the provider to dial."""

    ok: bool
    provider: str
    provider_call_id: str | None = None
    status: CallStatus = CallStatus.INITIATING
    detail: str | None = None       # sanitized (no secrets)


@dataclass(frozen=True)
class CallStatusUpdate:
    """A single call-status update parsed from a provider webhook/poll."""

    provider_call_id: str
    status: CallStatus
    raw_status: str | None = None   # the provider's own status string, for audit
    detail: str | None = None


@dataclass(frozen=True)
class AnswerDocument:
    """A provider-specific call-answer instruction document."""

    body: str
    media_type: str = "application/xml"


class TelephonyProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    async def initiate_outbound_call(self, req: CallRequest) -> CallResult:
        """Place one outbound call. Must NOT raise for ordinary failures —
        return a CallResult(ok=False, ...) so the caller can record it."""

    @abc.abstractmethod
    async def get_call_status(self, provider_call_id: str) -> CallStatusUpdate | None:
        """Poll the provider for the call's current status (None if unknown)."""

    @abc.abstractmethod
    async def terminate_call(self, provider_call_id: str) -> bool:
        """Best-effort hang up. Returns True if the provider accepted the request."""

    @abc.abstractmethod
    def verify_webhook(
        self, *, url: str, raw_body: bytes, form: dict[str, str], headers: dict[str, str]
    ) -> bool:
        """Return True iff an inbound provider webhook request is authentic."""

    @abc.abstractmethod
    def parse_status_webhook(self, form: dict[str, str]) -> CallStatusUpdate | None:
        """Translate a provider status callback into a CallStatusUpdate (or None)."""

    @abc.abstractmethod
    def build_call_answer_document(self, *, media_ws_url: str, booking_id: str) -> AnswerDocument:
        """Build the instruction document the provider fetches on answer, wiring
        the call's audio to our media-stream WebSocket."""

    @abc.abstractmethod
    def build_media_serializer(self, start_payload: dict):
        """Build the Pipecat FrameSerializer for this media stream from the
        provider's stream-'start' message. Returns a pipecat FrameSerializer."""

    @abc.abstractmethod
    def validate_configuration(self) -> list[str]:
        """Return a list of configuration problems (empty == ready). No network."""

    @property
    def ready(self) -> bool:
        return not self.validate_configuration()

    @property
    def is_mock(self) -> bool:
        return False
