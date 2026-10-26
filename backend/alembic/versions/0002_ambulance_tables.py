"""ambulance coordination tables

Revision ID: 0002_ambulance
Revises: 0001_initial
Create Date: 2026-10-26

Adds the ambulance directory, booking (with backend-owned state machine) and the
append-only call-event audit. SQLite dev also gets these via ``init_db``
create_all; this migration is what applies them to a production (Postgres) DB.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
import sqlmodel


revision: str = "0002_ambulance"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CALL_STATUS = sa.Enum(
    "PENDING", "INITIATING", "RINGING", "CONNECTED", "COMPLETED", "BUSY",
    "NO_ANSWER", "FAILED", "CANCELLED", name="callstatus",
)
_BOOKING_STATE = sa.Enum(
    "PREPARING_REQUEST", "VALIDATING_DETAILS", "INITIATING_CALL", "RINGING",
    "CONNECTED", "IDENTIFYING_OPERATOR", "EXPLAINING_EMERGENCY", "SHARING_LOCATION",
    "DISCUSSING_AVAILABILITY", "COLLECTING_DETAILS", "REQUESTING_CONFIRMATION",
    "BOOKING_CONFIRMED", "BOOKING_DECLINED", "CALL_FAILED", "CALL_DISCONNECTED",
    "PENDING_FOLLOW_UP", "COMPLETED", name="bookingstate",
)
_AVAILABILITY = sa.Enum("UNKNOWN", "AVAILABLE", "UNAVAILABLE", name="availability")


def upgrade() -> None:
    op.create_table(
        "ambulanceservice",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("name", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("phone", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("address", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("city", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("service_area", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("ambulance_types", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("capabilities", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("contact_person", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("operating_hours", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False),
        sa.Column("is_demo", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_by", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ambulanceservice", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ambulanceservice_phone"), ["phone"], unique=False)
        batch_op.create_index(batch_op.f("ix_ambulanceservice_created_by"), ["created_by"], unique=False)

    op.create_table(
        "ambulancebooking",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("incident_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("user_ref", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("service_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("patient_name", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("patient_age", sa.Integer(), nullable=True),
        sa.Column("patient_condition", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("emergency_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("num_patients", sa.Integer(), nullable=False),
        sa.Column("pickup_address", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("latitude", sa.Float(), nullable=True),
        sa.Column("longitude", sa.Float(), nullable=True),
        sa.Column("location_accuracy", sa.Float(), nullable=True),
        sa.Column("location_timestamp", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("destination_preference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("required_ambulance_type", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("special_requirements", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("authorized_contact", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("consent", sa.Boolean(), nullable=False),
        sa.Column("call_status", _CALL_STATUS, nullable=False),
        sa.Column("booking_state", _BOOKING_STATE, nullable=False),
        sa.Column("availability", _AVAILABILITY, nullable=False),
        sa.Column("booking_accepted", sa.Boolean(), nullable=False),
        sa.Column("receptionist_response", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("eta_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("charges_text", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("booking_reference", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("situation_code", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("provider", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("provider_call_id", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("call_initiated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("call_connected_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("call_completed_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.Column("updated_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("ambulancebooking", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_ambulancebooking_incident_id"), ["incident_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ambulancebooking_user_ref"), ["user_ref"], unique=False)
        batch_op.create_index(batch_op.f("ix_ambulancebooking_service_id"), ["service_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_ambulancebooking_booking_state"), ["booking_state"], unique=False)
        batch_op.create_index(batch_op.f("ix_ambulancebooking_provider_call_id"), ["provider_call_id"], unique=False)

    op.create_table(
        "callevent",
        sa.Column("id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("booking_id", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("event_type", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("from_state", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("to_state", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("detail", sqlmodel.sql.sqltypes.AutoString(), nullable=True),
        sa.Column("created_at", sqlmodel.sql.sqltypes.UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("callevent", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_callevent_booking_id"), ["booking_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("callevent", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_callevent_booking_id"))
    op.drop_table("callevent")

    with op.batch_alter_table("ambulancebooking", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ambulancebooking_provider_call_id"))
        batch_op.drop_index(batch_op.f("ix_ambulancebooking_booking_state"))
        batch_op.drop_index(batch_op.f("ix_ambulancebooking_service_id"))
        batch_op.drop_index(batch_op.f("ix_ambulancebooking_user_ref"))
        batch_op.drop_index(batch_op.f("ix_ambulancebooking_incident_id"))
    op.drop_table("ambulancebooking")

    with op.batch_alter_table("ambulanceservice", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_ambulanceservice_created_by"))
        batch_op.drop_index(batch_op.f("ix_ambulanceservice_phone"))
    op.drop_table("ambulanceservice")

    sa.Enum(name="bookingstate").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="callstatus").drop(op.get_bind(), checkfirst=True)
    sa.Enum(name="availability").drop(op.get_bind(), checkfirst=True)
