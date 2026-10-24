"""The SMS provider interface + shared value types.

Incident/notification logic depends ONLY on this interface, so a second provider
can be added later without touching the pipeline. The interface deliberately
separates three concerns the mission calls out:

  * send_emergency_sms      — hand one message to the carrier
  * parse_delivery_callback — turn a provider DLR webhook into internal updates
  * verify_callback         — authenticate that webhook
  * validate_configuration  — fail fast at startup with actionable errors
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum

from ...models import NotificationStatus


class SendOutcome(str, Enum):
    """The three honest outcomes of a single send attempt.

    ACCEPTED is NOT delivery — it only means the provider took the request.
    RETRYABLE is a transient failure (network/5xx/429) worth a backoff retry.
    PERMANENT is a non-retryable rejection (bad template, bad number, auth) —
    retrying it would just flood with the same failure.
    UNKNOWN means we could not determine the result (e.g. a timeout after the
    request may have been received) — must NOT be blindly retried.
    """

    ACCEPTED = "accepted"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OutboundSms:
    """One message to one recipient, as handed to a provider.

    ``to_e164`` is the channel destination: an E.164 phone for SMS providers, or
    an email address for the email provider. ``subject`` is used only by channels
    that have one (email); SMS providers ignore it.
    """

    to_e164: str
    body: str
    subject: str | None = None
    # Template variable map (for template-based providers like MSG91 DLT flows).
    variables: dict[str, str] = field(default_factory=dict)
    # Opaque idempotency hint the provider may use if supported.
    client_ref: str | None = None


@dataclass(frozen=True)
class SendResult:
    outcome: SendOutcome
    provider_message_id: str | None = None
    # Human-readable, already-sanitized (no secrets) error/explanation.
    detail: str | None = None
    # Optional structured retry-after seconds the provider asked us to honor.
    retry_after_seconds: float | None = None


@dataclass(frozen=True)
class DeliveryUpdate:
    """A single delivery-status update parsed from a provider callback."""

    provider_message_id: str
    status: NotificationStatus
    detail: str | None = None
    recipient_hint: str | None = None  # masked/partial, for correlation only


class SmsProvider(abc.ABC):
    name: str = "base"
    # The delivery channel this provider uses: "sms" or "email". Lets the one
    # outbox/worker drive different providers; the notifier picks the recipient
    # destination accordingly.
    channel: str = "sms"

    @abc.abstractmethod
    async def send_emergency_sms(self, msg: OutboundSms) -> SendResult:
        """Send one SMS. Must never raise for ordinary failures — return a
        SendResult with the appropriate SendOutcome so the worker can decide."""

    @abc.abstractmethod
    def parse_delivery_callback(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Translate a provider DLR webhook body into internal delivery updates.
        Returns an empty list if nothing actionable is present."""

    @abc.abstractmethod
    def verify_callback(self, *, secret: str | None) -> bool:
        """Return True iff the callback is authenticated. ``secret`` is the token
        the caller presented (e.g. from the callback URL)."""

    @abc.abstractmethod
    def validate_configuration(self) -> list[str]:
        """Return a list of configuration problems (empty list == ready).
        Must NOT contact the network or send anything."""

    @property
    def ready(self) -> bool:
        return not self.validate_configuration()
