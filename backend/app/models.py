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
    """Per-recipient delivery lifecycle.

    We deliberately separate *provider acceptance* from *confirmed delivery*:
    the provider taking the request (PROVIDER_ACCEPTED) is NOT proof the SMS
    reached the handset (DELIVERED). UNKNOWN is a real terminal-ish state used
    when a send timed out and we cannot safely assume accepted or failed.
    """

    PENDING = "pending"                    # queued in the outbox, not yet sent
    PROCESSING = "processing"              # a worker has claimed it and is sending
    PROVIDER_ACCEPTED = "provider_accepted"  # provider returned success/accepted
    DELIVERED = "delivered"                # provider DLR callback confirmed delivery
    FAILED = "failed"                      # permanent failure / attempts exhausted
    UNKNOWN = "unknown"                    # send result indeterminate (e.g. timeout)


# Terminal states a delivery-report callback must NOT regress away from.
TERMINAL_NOTIFICATION_STATES = {NotificationStatus.DELIVERED, NotificationStatus.FAILED}


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
    """A user's emergency contact. Owned by exactly one ``user_ref`` (E.164 phone).

    Phone numbers are stored in canonical E.164 so duplicate detection and
    provider formatting are unambiguous. A contact is never shared across users;
    every read/write is authorized against the owning ``user_ref``.
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    user_ref: str = Field(index=True)  # owner's E.164 phone (from the session)

    name: str
    phone_e164: str = Field(index=True)
    # Optional email destination. Required only when the EMAIL channel is active
    # (the free, DLT-free alerting path). Phone stays the stable identity + dedupe key.
    email: str | None = None
    country: str = "IN"
    relationship: str | None = None
    # Lower number = contacted first. Ties broken by created_at.
    priority: int = Field(default=100)
    # Whether this contact receives SOS alerts.
    active: bool = Field(default=True)
    # We only mark verified once a real confirmation flow succeeds — saving a
    # contact does NOT make it verified.
    verified: bool = Field(default=False)

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class NotificationJob(SQLModel, table=True):
    """Durable outbox row + per-recipient notification attempt record.

    One row per (incident, recipient). It is simultaneously:
      * the transactional outbox entry the background worker claims and sends, and
      * the auditable attempt record (status, attempts, provider id, errors).

    Recipient name/phone and the message body are SNAPSHOTTED here at creation so
    the audit trail reflects exactly what was intended to be sent, even if the
    contact is later edited or deleted.
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    incident_id: str = Field(index=True)
    user_ref: str = Field(index=True)
    contact_id: str | None = None  # may be null if the contact was later deleted

    # Snapshots (immutable audit of what we intended to send).
    recipient_name: str | None = None
    recipient_phone: str = Field(index=True)  # E.164 (always captured for audit)
    recipient_email: str | None = None        # set when channel == "email"
    # Which channel this job is delivered over ("sms" | "email"). Lets one outbox
    # drive multiple providers without changing the pipeline.
    channel: str = Field(default="sms")
    subject: str | None = None                # email subject (null for SMS)
    message_body: str = ""
    # JSON-encoded template variables (for DLT template providers like MSG91).
    # The rendered body above is the intended/audited content; these drive the
    # carrier's approved-template substitution.
    template_vars: str | None = None

    status: NotificationStatus = Field(default=NotificationStatus.PENDING, index=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=4)

    provider: str | None = None
    provider_message_id: str | None = Field(default=None, index=True)
    last_error: str | None = None  # sanitized

    # Outbox scheduling + worker lease.
    next_attempt_at: datetime = Field(default_factory=utcnow, index=True)
    locked_at: datetime | None = None
    locked_by: str | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    delivered_at: datetime | None = None


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
    phone: str                       # raw; normalized to E.164 server-side
    email: str | None = None         # required for the email alert channel
    country: str | None = None       # ISO region hint for numbers without '+'
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
    phone: str | None = None
    email: str | None = None
    country: str | None = None
    relationship: str | None = None
    priority: int | None = None
    active: bool | None = None


class ContactRead(BaseModel):
    id: str
    name: str
    phone_e164: str
    email: str | None
    country: str
    relationship: str | None
    priority: int
    active: bool
    verified: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── SOS + notification schemas ────────────────────────────────────────────────
class SosRequest(BaseModel):
    """Payload the authenticated app sends to trigger an emergency.

    Note what is ABSENT: no recipient phone numbers. Recipients are resolved
    server-side from the authenticated user's stored contacts — a caller can
    never direct SMS at arbitrary numbers through this endpoint.
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
    recipient_phone: str
    recipient_email: str | None
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
