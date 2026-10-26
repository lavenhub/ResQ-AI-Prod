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


# ═══════════════════════════════════════════════════════════════════════════════
# Ambulance coordination — directory, booking + backend-owned state machine
# ═══════════════════════════════════════════════════════════════════════════════
# These power the REAL outbound-call feature: ResQAI phones a configured
# hospital/ambulance reception number and an AI coordinates a booking. The
# cardinal rule (mirrors the email pipeline's honesty discipline) is that these
# are DIFFERENT, non-conflated facts and only the BACKEND advances them:
#   available  — the receptionist said an ambulance exists
#   accepted   — the receptionist agreed to take the request
#   confirmed  — explicit confirmation received AND recorded by the backend
#   dispatched — an ambulance physically left (NEVER auto-set from a call)
# The LLM reports observations through a validated tool; it can never set a
# confirmed/dispatched state on its own.


class CallStatus(str, Enum):
    """Telephony-level call lifecycle (provider-driven)."""

    PENDING = "PENDING"          # booking made, no call placed yet
    INITIATING = "INITIATING"    # we asked the provider to dial
    RINGING = "RINGING"          # provider reports the far end is ringing
    CONNECTED = "CONNECTED"      # the receptionist answered; media is flowing
    COMPLETED = "COMPLETED"      # call ended normally
    BUSY = "BUSY"                # far end busy
    NO_ANSWER = "NO_ANSWER"      # rang out, nobody answered
    FAILED = "FAILED"            # provider/network error, invalid number, etc.
    CANCELLED = "CANCELLED"      # we terminated it


# Terminal call statuses (no further telephony progress expected).
TERMINAL_CALL_STATES = {
    CallStatus.COMPLETED,
    CallStatus.BUSY,
    CallStatus.NO_ANSWER,
    CallStatus.FAILED,
    CallStatus.CANCELLED,
}
# Call statuses that mean "a call is in flight" — used to block duplicate dials.
IN_FLIGHT_CALL_STATES = {
    CallStatus.INITIATING,
    CallStatus.RINGING,
    CallStatus.CONNECTED,
}


class BookingState(str, Enum):
    """Backend-owned booking state machine (mission Phase 7)."""

    PREPARING_REQUEST = "PREPARING_REQUEST"
    VALIDATING_DETAILS = "VALIDATING_DETAILS"
    INITIATING_CALL = "INITIATING_CALL"
    RINGING = "RINGING"
    CONNECTED = "CONNECTED"
    IDENTIFYING_OPERATOR = "IDENTIFYING_OPERATOR"
    EXPLAINING_EMERGENCY = "EXPLAINING_EMERGENCY"
    SHARING_LOCATION = "SHARING_LOCATION"
    DISCUSSING_AVAILABILITY = "DISCUSSING_AVAILABILITY"
    COLLECTING_DETAILS = "COLLECTING_DETAILS"
    REQUESTING_CONFIRMATION = "REQUESTING_CONFIRMATION"
    BOOKING_CONFIRMED = "BOOKING_CONFIRMED"
    BOOKING_DECLINED = "BOOKING_DECLINED"
    CALL_FAILED = "CALL_FAILED"
    CALL_DISCONNECTED = "CALL_DISCONNECTED"
    PENDING_FOLLOW_UP = "PENDING_FOLLOW_UP"
    COMPLETED = "COMPLETED"


# Terminal booking states (the workflow has reached an outcome).
TERMINAL_BOOKING_STATES = {
    BookingState.BOOKING_CONFIRMED,
    BookingState.BOOKING_DECLINED,
    BookingState.CALL_FAILED,
    BookingState.CALL_DISCONNECTED,
    BookingState.COMPLETED,
    BookingState.PENDING_FOLLOW_UP,
}


class Availability(str, Enum):
    """What the receptionist said about ambulance availability (an observation,
    NOT a booking outcome)."""

    UNKNOWN = "UNKNOWN"
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"


# The call situations the AI must distinguish (mission Phase 6, A–N).
SITUATION_CODES = {
    "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M", "N",
}


# ── Persisted models ───────────────────────────────────────────────────────────
class AmbulanceService(SQLModel, table=True):
    """A configurable hospital / ambulance directory entry (the dial target).

    A number is only an eligible call target when it is ``active`` AND
    (``verified`` OR ``is_demo``). An unverified, non-demo number is NEVER treated
    as an official emergency service and is never dialed. Arbitrary numbers
    supplied by the LLM or a user are rejected — the target must resolve to a row
    here (mission Phase 3).
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    phone: str = Field(index=True)            # E.164, normalized on write
    address: str | None = None
    city: str | None = None
    service_area: str | None = None
    ambulance_types: str | None = None        # free text / comma list, if known
    capabilities: str | None = None           # e.g. "ICU, ventilator, neonatal"
    contact_person: str | None = None
    operating_hours: str | None = None
    verified: bool = Field(default=False)
    # True for the team-controlled demo reception number. Clearly labelled in the
    # UI and allowed as a call target without full verification.
    is_demo: bool = Field(default=False)
    active: bool = Field(default=True)
    created_by: str | None = Field(default=None, index=True)  # admin user_ref
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class AmbulanceBooking(SQLModel, table=True):
    """A structured ambulance-booking request tied to an existing Incident.

    Patient/location facts are copied from the Incident at creation (single
    source of truth; no duplicate patient model). Every field the receptionist
    provides (ETA, charges, reference) stays null until it is actually given —
    we never invent it.
    """

    id: str = Field(default_factory=new_id, primary_key=True)
    incident_id: str = Field(index=True)
    user_ref: str = Field(index=True)          # owner (authenticated caller)
    service_id: str = Field(index=True)        # -> AmbulanceService

    # Patient / emergency (verified facts; copied from incident / supplied by user)
    patient_name: str | None = None
    patient_age: int | None = None
    patient_condition: str | None = None
    emergency_type: str | None = None
    num_patients: int = Field(default=1)

    # Pickup location (copied from the incident; device-reported, never invented)
    pickup_address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    location_timestamp: datetime | None = None

    destination_preference: str | None = None
    required_ambulance_type: str | None = None
    special_requirements: str | None = None
    # Who authorized this on the caller's side (name/relationship — not a secret).
    authorized_contact: str | None = None
    # Explicit consent to share the patient's details with the service.
    consent: bool = Field(default=False)

    # ── Telephony + booking state (backend-owned) ────────────────────────────
    call_status: CallStatus = Field(default=CallStatus.PENDING)
    booking_state: BookingState = Field(default=BookingState.PREPARING_REQUEST, index=True)
    availability: Availability = Field(default=Availability.UNKNOWN)
    # Distinct from confirmed: the receptionist agreed to take it.
    booking_accepted: bool = Field(default=False)

    # Receptionist-provided outcome details — null until actually provided.
    receptionist_response: str | None = None
    eta_text: str | None = None
    charges_text: str | None = None
    booking_reference: str | None = None
    situation_code: str | None = None          # A–N (mission Phase 6)

    provider: str | None = None
    provider_call_id: str | None = Field(default=None, index=True)
    attempts: int = Field(default=0)
    last_error: str | None = None

    call_initiated_at: datetime | None = None
    call_connected_at: datetime | None = None
    call_completed_at: datetime | None = None

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CallEvent(SQLModel, table=True):
    """Append-only audit of every state/call transition for a booking.

    Lets the whole call+booking lifecycle be inspected after the fact, and makes
    duplicate/out-of-order provider events visible. Never the source of truth for
    the current state (that lives on ``AmbulanceBooking``)."""

    id: str = Field(default_factory=new_id, primary_key=True)
    booking_id: str = Field(index=True)
    event_type: str                       # e.g. "state_transition", "call_status", "tool"
    from_state: str | None = None
    to_state: str | None = None
    detail: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


# ── Ambulance directory schemas ────────────────────────────────────────────────
class AmbulanceServiceCreate(BaseModel):
    name: str
    phone: str
    address: str | None = None
    city: str | None = None
    service_area: str | None = None
    ambulance_types: str | None = None
    capabilities: str | None = None
    contact_person: str | None = None
    operating_hours: str | None = None
    is_demo: bool = False

    @field_validator("name")
    @classmethod
    def _name_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("name is required")
        return v.strip()


class AmbulanceServiceUpdate(BaseModel):
    name: str | None = None
    phone: str | None = None
    address: str | None = None
    city: str | None = None
    service_area: str | None = None
    ambulance_types: str | None = None
    capabilities: str | None = None
    contact_person: str | None = None
    operating_hours: str | None = None
    active: bool | None = None


class AmbulanceServiceRead(BaseModel):
    id: str
    name: str
    phone: str            # masked for display in the route layer
    address: str | None
    city: str | None
    service_area: str | None
    ambulance_types: str | None
    capabilities: str | None
    contact_person: str | None
    operating_hours: str | None
    verified: bool
    is_demo: bool
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── Booking schemas ─────────────────────────────────────────────────────────────
class BookingCreate(BaseModel):
    """Create a booking.

    Provide ``incident_id`` to tie it to an existing incident the caller owns
    (location is then pulled from that incident). If omitted, a new incident is
    created server-side, owned by the authenticated caller, from the location +
    emergency fields below — so the client never has to assert an owner itself.
    """

    incident_id: str | None = None
    service_id: str
    patient_name: str | None = None
    patient_age: int | None = None
    patient_condition: str | None = None
    emergency_type: str | None = None
    num_patients: int = 1
    destination_preference: str | None = None
    required_ambulance_type: str | None = None
    special_requirements: str | None = None
    authorized_contact: str | None = None
    # Must be true to place a call that shares the patient's details.
    consent: bool = False

    # Used ONLY when incident_id is absent, to seed the auto-created incident.
    latitude: float | None = None
    longitude: float | None = None
    location_accuracy: float | None = None
    address: str | None = None

    _v_lat = field_validator("latitude")(_validate_lat)
    _v_lng = field_validator("longitude")(_validate_lng)
    _v_acc = field_validator("location_accuracy")(_validate_accuracy)

    @field_validator("num_patients")
    @classmethod
    def _at_least_one(cls, v: int) -> int:
        if v is None or v < 1:
            return 1
        return v


class CallEventRead(BaseModel):
    event_type: str
    from_state: str | None
    to_state: str | None
    detail: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class BookingRead(BaseModel):
    id: str
    incident_id: str
    service_id: str
    # Flattened service summary (number is masked; the frontend never sees the
    # full target number).
    service_name: str | None = None
    service_phone_masked: str | None = None
    service_is_demo: bool = False

    patient_name: str | None
    patient_age: int | None
    patient_condition: str | None
    emergency_type: str | None
    num_patients: int
    pickup_address: str | None
    latitude: float | None
    longitude: float | None
    destination_preference: str | None
    required_ambulance_type: str | None
    special_requirements: str | None
    authorized_contact: str | None
    consent: bool

    call_status: CallStatus
    booking_state: BookingState
    availability: Availability
    booking_accepted: bool
    receptionist_response: str | None
    eta_text: str | None
    charges_text: str | None
    booking_reference: str | None
    situation_code: str | None
    provider: str | None
    attempts: int
    last_error: str | None
    call_initiated_at: datetime | None
    call_connected_at: datetime | None
    call_completed_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
