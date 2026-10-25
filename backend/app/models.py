"""Incident data model + validated request/response schemas.

Field names deliberately mirror the frontend's `BriefingReport`
(`src/lib/types.ts`) and `useVoiceAgent` so Phase 3 (voice → incident) needs no
translation layer. Everything the user has NOT reported stays `null` — we never
fabricate details.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, field_validator
from sqlmodel import Field, SQLModel


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class IncidentStatus(str, Enum):
    NEW = "NEW"              # created, nothing gathered yet
    GATHERING = "GATHERING"  # agent is actively collecting details
    ACTIVE = "ACTIVE"        # confirmed active emergency
    RESOLVED = "RESOLVED"    # session ended normally
    CANCELLED = "CANCELLED"  # false alarm / user cancelled


class IncidentSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNKNOWN = "UNKNOWN"


class TriState(str, Enum):
    """For victim vitals the agent may or may not have confirmed."""

    YES = "Yes"
    NO = "No"
    UNKNOWN = "Unknown"


class TriggerSource(str, Enum):
    """How an incident came to exist."""

    APP = "app"          # user tapped SOS in the app
    VOICE = "voice"      # created by the AI voice dispatcher tool
    UNKNOWN = "unknown"


class NotificationStatus(str, Enum):
    """Per-recipient email delivery lifecycle.

    These statuses are deliberately TRUTHFUL and never conflated:
      * ACCEPTED_BY_PROVIDER means the email provider (Resend) took the request —
        it is NOT proof the message reached the recipient's mailbox.
      * DELIVERED is set only when the provider's webhook reports actual delivery.
      * DEFERRED is a temporary provider-side delay (delivery still being retried).
      * BOUNCED is a provider-reported hard/soft bounce (the mailbox rejected it).
      * UNKNOWN is used when a send result is indeterminate (e.g. a timeout where
        the provider may or may not have accepted it) — never blindly retried.
      * CANCELLED is set when the incident is cancelled before the message is sent.

    We never claim "opened" or "read": delivery is as far as truthful tracking
    goes without invasive, consented mechanisms we deliberately do not use.
    """

    QUEUED = "queued"                      # durably persisted in the outbox, awaiting a worker
    PROCESSING = "processing"              # a worker claimed it and is sending
    ACCEPTED_BY_PROVIDER = "accepted_by_provider"  # provider accepted the request (not delivery)
    DELIVERED = "delivered"                # provider webhook confirmed mailbox delivery
    DEFERRED = "deferred"                  # provider reported a temporary delivery delay
    BOUNCED = "bounced"                    # provider reported a bounce (mailbox rejected)
    FAILED = "failed"                      # permanent failure / attempts exhausted / rejected
    UNKNOWN = "unknown"                    # send result indeterminate (e.g. timeout)
    CANCELLED = "cancelled"                # incident cancelled before the message was sent
    # Reserved pre-queue state (kept for API stability; the outbox starts at QUEUED).
    PENDING = "pending"


# Terminal states a provider webhook must NOT regress away from (guards against
# duplicate / out-of-order delivery events).
TERMINAL_NOTIFICATION_STATES = {
    NotificationStatus.DELIVERED,
    NotificationStatus.BOUNCED,
    NotificationStatus.FAILED,
    NotificationStatus.CANCELLED,
}


# ── Persisted model ──────────────────────────────────────────────────────────
class Incident(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    # Ties an incident to one voice session so retries can't create duplicates.
    session_id: str | None = Field(default=None, index=True)
    user_ref: str | None = Field(default=None, index=True)  # phone; no real auth yet

    incident_type: str | None = None
    description: str | None = None
    reported_symptoms: str | None = None
    reported_hazards: str | None = None

    # Location is set from the device (offer payload / tool), never invented by the LLM.
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None

    detected_language: str | None = None
    severity: IncidentSeverity = Field(default=IncidentSeverity.UNKNOWN)
    status: IncidentStatus = Field(default=IncidentStatus.NEW)

    conscious: TriState | None = None
    breathing: TriState | None = None
    bleeding: TriState | None = None

    # ── SOS / alerting metadata ──────────────────────────────────────────
    trigger_source: TriggerSource = Field(default=TriggerSource.UNKNOWN)
    # A short, caller/agent-provided note (not medical PII dumps).
    note: str | None = None
    # Client-supplied idempotency key, unique PER USER (enforced in service),
    # so a double-tap / retried SOS resolves to the same incident.
    idempotency_key: str | None = Field(default=None, index=True)
    cancelled_at: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class EmergencyContact(SQLModel, table=True):
    """A user's emergency EMAIL contact. Owned by exactly one ``user_ref``.

    The destination is an **email address** (normalized lowercase), which is also
    the per-user dedupe key. A contact is never shared across users; every
    read/write is authorized against the owning ``user_ref``.

    A contact is only eligible to receive an SOS alert when it is ``active`` AND
    ``verified`` AND not ``suppressed`` — saving a contact never implies any of
    these. Verification is a real, token-based email-ownership check (see
    ``services.verification``), not merely "the address was typed in".
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    user_ref: str = Field(index=True)  # owner's E.164 phone (from the session)

    name: str
    email: str = Field(index=True)  # normalized lowercase; unique per user (service-enforced)
    relationship: str | None = None
    # Lower number = contacted first. Ties broken by created_at.
    priority: int = Field(default=100)
    # Whether this contact receives SOS alerts (user toggle / consent preference).
    active: bool = Field(default=True)

    # ── Email-ownership verification ─────────────────────────────────────────
    # We only mark verified once the recipient clicks a single-use link; saving a
    # contact does NOT make it verified. Only a salted hash of the token is stored.
    verified: bool = Field(default=False)
    verified_at: datetime | None = None
    verification_token_hash: str | None = None
    verification_sent_at: datetime | None = None
    verification_expires_at: datetime | None = None

    # ── Suppression (bounce / spam complaint) ────────────────────────────────
    # Set when the provider reports a hard bounce or a spam complaint, so we stop
    # emailing an address that rejects us (deliverability + anti-abuse hygiene).
    suppressed: bool = Field(default=False)
    suppressed_reason: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class NotificationJob(SQLModel, table=True):
    """Durable outbox row + per-recipient email notification attempt record.

    One row per (incident, recipient). It is simultaneously:
      * the transactional outbox entry the background worker claims and sends, and
      * the auditable attempt record (status, attempts, provider id, errors).

    Recipient name/email and the rendered message (subject + text + HTML) are
    SNAPSHOTTED here at creation so the audit trail reflects exactly what was
    intended to be sent, even if the contact is later edited or deleted.
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    incident_id: str = Field(index=True)
    user_ref: str = Field(index=True)
    contact_id: str | None = None  # may be null if the contact was later deleted

    # Snapshots (immutable audit of what we intended to send).
    recipient_name: str | None = None
    recipient_email: str = Field(index=True)
    # Delivery channel. Email-only today, but kept so the outbox can drive a second
    # channel later without a schema change.
    channel: str = Field(default="email")
    subject: str | None = None
    message_body: str = ""            # plain-text part (audited intended content)
    html_body: str | None = None      # HTML part (audited intended content)

    status: NotificationStatus = Field(default=NotificationStatus.QUEUED, index=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=4)

    provider: str | None = None
    # The provider's message id (Resend email id) — the correlation key webhooks
    # use to connect a delivery event back to this job.
    provider_message_id: str | None = Field(default=None, index=True)
    # Stable key sent to the provider as an Idempotency-Key, so a retried send
    # after an ambiguous timeout cannot create a duplicate email within the
    # provider's dedupe window.
    idempotency_key: str | None = Field(default=None, index=True)
    last_error: str | None = None  # sanitized

    # Outbox scheduling + worker lease.
    next_attempt_at: datetime = Field(default_factory=utcnow, index=True)
    locked_at: datetime | None = None
    locked_by: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None


class NotificationEvent(SQLModel, table=True):
    """Audit record of a single provider delivery event (webhook).

    Persisted so delivery history is inspectable and so duplicate webhook
    deliveries are visible. Never used as the source of truth for the current
    status (that lives on ``NotificationJob``) — this is the append-only log.
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    notification_id: str | None = Field(default=None, index=True)
    provider_message_id: str | None = Field(default=None, index=True)
    event_type: str                       # provider event name, e.g. "email.delivered"
    mapped_status: str | None = None       # the internal status it mapped to
    detail: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ── Shared validators ────────────────────────────────────────────────────────
def _validate_lat(v: float | None) -> float | None:
    if v is not None and not (-90.0 <= v <= 90.0):
        raise ValueError("latitude must be between -90 and 90")
    return v


def _validate_lng(v: float | None) -> float | None:
    if v is not None and not (-180.0 <= v <= 180.0):
        raise ValueError("longitude must be between -180 and 180")
    return v


def _validate_accuracy(v: float | None) -> float | None:
    if v is not None and v < 0:
        raise ValueError("location_accuracy must be >= 0")
    return v


# ── Request / response schemas ───────────────────────────────────────────────
class IncidentCreate(BaseModel):
    session_id: str | None = None
    user_ref: str | None = None
    incident_type: str | None = None
    description: str | None = None
    reported_symptoms: str | None = None
    reported_hazards: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None
    detected_language: str | None = None
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    conscious: TriState | None = None
    breathing: TriState | None = None
    bleeding: TriState | None = None

    _v_lat = field_validator("latitude")(_validate_lat)
    _v_lng = field_validator("longitude")(_validate_lng)
    _v_acc = field_validator("location_accuracy")(_validate_accuracy)


class IncidentUpdate(BaseModel):
    """Partial update — only fields explicitly set are applied (PATCH semantics)."""

    incident_type: str | None = None
    description: str | None = None
    reported_symptoms: str | None = None
    reported_hazards: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None
    detected_language: str | None = None
    severity: IncidentSeverity | None = None
    status: IncidentStatus | None = None
    conscious: TriState | None = None
    breathing: TriState | None = None
    bleeding: TriState | None = None

    _v_lat = field_validator("latitude")(_validate_lat)
    _v_lng = field_validator("longitude")(_validate_lng)
    _v_acc = field_validator("location_accuracy")(_validate_accuracy)


class IncidentRead(BaseModel):
    id: str
    session_id: str | None
    user_ref: str | None
    incident_type: str | None
    description: str | None
    reported_symptoms: str | None
    reported_hazards: str | None
    latitude: float | None
    longitude: float | None
    location_accuracy: float | None
    address: str | None
    detected_language: str | None
    severity: IncidentSeverity
    status: IncidentStatus
    conscious: TriState | None
    breathing: TriState | None
    bleeding: TriState | None
    trigger_source: TriggerSource
    note: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Emergency contact schemas ─────────────────────────────────────────────────
class ContactCreate(BaseModel):
    name: str
    email: str                       # normalized + validated server-side
    relationship: str | None = None
    priority: int = 100
    active: bool = True

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name is required")
        return v.strip()


class ContactUpdate(BaseModel):
    name: str | None = None
    email: str | None = None
    relationship: str | None = None
    priority: int | None = None
    active: bool | None = None


class ContactRead(BaseModel):
    id: str
    name: str
    email: str
    relationship: str | None
    priority: int
    active: bool
    verified: bool
    verified_at: datetime | None
    suppressed: bool
    suppressed_reason: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class VerificationStartResponse(BaseModel):
    """Result of (re)sending a contact verification email."""

    contact_id: str
    sent: bool
    # Set ONLY when a non-real (mock) provider is configured, so the demo/tests can
    # complete the verification loop without a live mailbox. Never populated for a
    # real provider — the link then only ever reaches the recipient's inbox.
    dev_verification_url: str | None = None
    detail: str | None = None


# ── SOS + notification schemas ────────────────────────────────────────────────
class SosRequest(BaseModel):
    """Payload the authenticated app sends to trigger an emergency.

    Note what is ABSENT: no recipient addresses. Recipients are resolved
    server-side from the authenticated user's stored, verified contacts — a
    caller can never direct email at arbitrary addresses through this endpoint.
    """

    incident_type: str | None = None
    note: str | None = None
    # The caller's own display name, shown to THEIR contacts. Not a security
    # input (no authorization is derived from it) — just alert content.
    victim_name: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None
    # Client-provided reading time (ISO8601). Validated; may be absent.
    location_timestamp: datetime | None = None
    idempotency_key: str | None = None

    _v_lat = field_validator("latitude")(_validate_lat)
    _v_lng = field_validator("longitude")(_validate_lng)
    _v_acc = field_validator("location_accuracy")(_validate_accuracy)


class NotificationRead(BaseModel):
    id: str
    incident_id: str
    recipient_name: str | None
    recipient_email: str
    channel: str
    status: NotificationStatus
    attempts: int
    max_attempts: int
    provider: str | None
    provider_message_id: str | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime
    delivered_at: datetime | None

    model_config = {"from_attributes": True}


class SosResponse(BaseModel):
    incident_id: str
    created: bool                    # False when idempotency returned an existing one
    status: IncidentStatus
    location_available: bool
    notifications: list[NotificationRead]
    # Derived aggregate across recipients (queued/processing/partially_notified/…)
    notification_summary: dict
