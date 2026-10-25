"""The email provider interface + shared value types.

Notification/incident logic depends ONLY on this interface, so a second provider
(e.g. SendGrid) can be added later without touching the pipeline. The interface
separates the concerns the mission calls out:

  * send                    — hand one email to the provider
  * validate_configuration  — fail fast at startup with actionable errors
  * verify_webhook          — authenticate an inbound delivery-event webhook
  * parse_webhook_event     — turn a provider webhook into internal delivery updates

Bounce and complaint handling are expressed through ``parse_webhook_event`` (which
maps those events to ``NotificationStatus``/``DeliveryUpdate``) plus the webhook
route, which acts on them (suppressing the offending contact).
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from enum import Enum

from ...models import NotificationStatus


class SendOutcome(str, Enum):
    """The honest outcomes of a single send attempt.

    ACCEPTED is NOT delivery — it only means the provider took the request.
    RETRYABLE is a transient failure (network/5xx/429) worth a backoff retry.
    PERMANENT is a non-retryable rejection (bad key, invalid recipient) —
    retrying it would just repeat the same failure.
    UNKNOWN means we could not determine the result (e.g. a timeout after the
    request may already have been accepted) — must NOT be blindly retried.
    """

    ACCEPTED = "accepted"
    RETRYABLE = "retryable"
    PERMANENT = "permanent"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class OutboundEmail:
    """One email to one recipient, as handed to a provider."""

    to_email: str
    subject: str
    text: str
    html: str | None = None
    # Opaque idempotency key the provider may use to dedupe identical sends.
    idempotency_key: str | None = None
    # Optional classification tags (provider-specific; best-effort).
    tags: dict[str, str] = field(default_factory=dict)


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
    """A single delivery-status update parsed from a provider webhook.

    ``status`` may be None for events that should be recorded for audit but must
    not change the job's status (e.g. a spam complaint, whose real effect is
    suppressing the contact rather than altering a delivery outcome).
    """

    provider_message_id: str
    event_type: str                    # provider event name, e.g. "email.delivered"
    status: NotificationStatus | None = None
    detail: str | None = None
    # True for a spam complaint, so the webhook route can suppress the contact.
    is_complaint: bool = False
    # True for a hard bounce, so the webhook route can suppress the contact.
    is_bounce: bool = False


class EmailProvider(abc.ABC):
    name: str = "base"
    channel: str = "email"

    @abc.abstractmethod
    async def send(self, msg: OutboundEmail) -> SendResult:
        """Send one email. Must never raise for ordinary failures — return a
        SendResult with the appropriate SendOutcome so the worker can decide."""

    @abc.abstractmethod
    def parse_webhook_event(
        self, payload: dict, headers: dict[str, str] | None = None
    ) -> list[DeliveryUpdate]:
        """Translate a provider webhook body into internal delivery updates.
        Returns an empty list if nothing actionable is present."""

    @abc.abstractmethod
    def verify_webhook(
        self, *, raw_body: bytes, headers: dict[str, str]
    ) -> bool:
        """Return True iff the webhook request is authentic (signature valid and,
        where applicable, within the allowed timestamp tolerance)."""

    @abc.abstractmethod
    def validate_configuration(self) -> list[str]:
        """Return a list of configuration problems (empty list == ready).
        Must NOT contact the network or send anything."""

    @property
    def ready(self) -> bool:
        return not self.validate_configuration()

    @property
    def is_mock(self) -> bool:
        return False
