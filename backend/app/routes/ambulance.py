"""Ambulance coordination API (authenticated).

Endpoints:
  * directory (hospital/ambulance services): list / create / edit / verify /
    deactivate — the configurable dial targets (mission Phase 3).
  * bookings: create from an incident, place the real outbound call, poll status,
    retry, cancel (mission Phases 4, 5, 7).

Backend authorization + number allowlisting live in ``ambulance_service``: a call
is only ever placed to an ``active`` + (``verified`` or ``is_demo``) directory
number, never to an arbitrary number supplied by the LLM or the client.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger
from sqlmodel import Session, select

from .. import ambulance_service
from ..ambulance_service import BookingError
from ..auth import get_current_user
from ..config import get_settings
from ..db import get_session
from ..models import (
    AmbulanceBooking,
    AmbulanceService,
    AmbulanceServiceCreate,
    AmbulanceServiceRead,
    AmbulanceServiceUpdate,
    BookingCreate,
    BookingRead,
    CallStatus,
    utcnow,
)
from ..services.phone import PhoneValidationError, mask_phone, normalize_phone
from ..services.telephony import CallRequest, get_telephony_provider

router = APIRouter(prefix="/api/ambulance")


# ── serialization helpers ──────────────────────────────────────────────────────
def _service_read(svc: AmbulanceService) -> AmbulanceServiceRead:
    r = AmbulanceServiceRead.model_validate(svc)
    # Never expose the full target number to the client — mask it.
    r.phone = mask_phone(svc.phone)
    return r


def _booking_read(session: Session, booking: AmbulanceBooking) -> BookingRead:
    r = BookingRead.model_validate(booking)
    svc = session.get(AmbulanceService, booking.service_id)
    if svc is not None:
        r.service_name = svc.name
        r.service_phone_masked = mask_phone(svc.phone)
        r.service_is_demo = svc.is_demo
    return r


# ═══════════════════════════ Directory ═══════════════════════════════════════
@router.get("/services", response_model=list[AmbulanceServiceRead])
async def list_services(
    include_inactive: bool = False,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[AmbulanceServiceRead]:
    stmt = select(AmbulanceService).order_by(AmbulanceService.created_at.desc())  # type: ignore[attr-defined]
    if not include_inactive:
        stmt = stmt.where(AmbulanceService.active == True)  # noqa: E712
    return [_service_read(s) for s in session.exec(stmt).all()]


@router.post("/services", response_model=AmbulanceServiceRead, status_code=status.HTTP_201_CREATED)
async def create_service(
    body: AmbulanceServiceCreate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> AmbulanceServiceRead:
    try:
        e164 = normalize_phone(body.phone).e164
    except PhoneValidationError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid phone number: {exc}") from exc
    svc = AmbulanceService(
        name=body.name,
        phone=e164,
        address=body.address,
        city=body.city,
        service_area=body.service_area,
        ambulance_types=body.ambulance_types,
        capabilities=body.capabilities,
        contact_person=body.contact_person,
        operating_hours=body.operating_hours,
        is_demo=bool(body.is_demo),
        verified=False,          # verification is an explicit admin action
        active=True,
        created_by=user_ref,
    )
    session.add(svc)
    session.commit()
    session.refresh(svc)
    return _service_read(svc)


@router.patch("/services/{service_id}", response_model=AmbulanceServiceRead)
async def update_service(
    service_id: str,
    body: AmbulanceServiceUpdate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> AmbulanceServiceRead:
    svc = session.get(AmbulanceService, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    changes = body.model_dump(exclude_unset=True)
    if "phone" in changes and changes["phone"]:
        try:
            changes["phone"] = normalize_phone(changes["phone"]).e164
        except PhoneValidationError as exc:
            raise HTTPException(status_code=422, detail=f"Invalid phone number: {exc}") from exc
    for k, v in changes.items():
        setattr(svc, k, v)
    svc.updated_at = utcnow()
    session.add(svc)
    session.commit()
    session.refresh(svc)
    return _service_read(svc)


@router.post("/services/{service_id}/verify", response_model=AmbulanceServiceRead)
async def verify_service(
    service_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> AmbulanceServiceRead:
    svc = session.get(AmbulanceService, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    svc.verified = True
    svc.updated_at = utcnow()
    session.add(svc)
    session.commit()
    session.refresh(svc)
    return _service_read(svc)


@router.delete("/services/{service_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_service(
    service_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    svc = session.get(AmbulanceService, service_id)
    if svc is None:
        raise HTTPException(status_code=404, detail="Service not found")
    session.delete(svc)
    session.commit()


# ═══════════════════════════ Bookings ════════════════════════════════════════
@router.post("/bookings", response_model=BookingRead, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: BookingCreate,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    try:
        booking = ambulance_service.create_booking(session, user_ref, body)
    except BookingError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _booking_read(session, booking)


@router.get("/bookings", response_model=list[BookingRead])
async def list_bookings(
    incident_id: str | None = None,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> list[BookingRead]:
    rows = ambulance_service.list_bookings(session, user_ref, incident_id=incident_id)
    return [_booking_read(session, b) for b in rows]


@router.get("/bookings/{booking_id}", response_model=BookingRead)
async def get_booking(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    return _booking_read(session, booking)


@router.post("/bookings/{booking_id}/call", response_model=BookingRead)
async def place_call(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    """Place the REAL outbound call for this booking.

    Validates consent + the target number + duplicate-call prevention, reserves
    the in-flight slot, then asks the telephony provider to dial. The provider
    calls back on our TwiML/status/media endpoints to drive the conversation and
    the state machine.
    """
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")

    settings = get_settings()
    provider = get_telephony_provider()
    if not provider.is_mock:
        cfg_problems = provider.validate_configuration()
        if cfg_problems:
            raise HTTPException(
                status_code=503,
                detail="Telephony provider not configured: " + "; ".join(cfg_problems),
            )

    problems = ambulance_service.validate_for_call(session, booking)
    if problems:
        raise HTTPException(status_code=409, detail="; ".join(problems))

    to_number = ambulance_service.service_dial_number(session, booking)

    # Reserve BEFORE dialing to close the duplicate-call window.
    booking = ambulance_service.reserve_call(session, booking)

    base = settings.telephony_base_url.rstrip("/")
    ws_base = settings.media_ws_base().rstrip("/")
    req = CallRequest(
        to_number=to_number,
        from_number=settings.telephony_caller_id,
        booking_id=booking.id,
        answer_url=f"{base}/api/ambulance/telephony/twiml/{booking.id}",
        status_callback_url=f"{base}/api/ambulance/telephony/status/{booking.id}",
        media_ws_url=f"{ws_base}/api/ambulance/telephony/media/{booking.id}",
        max_seconds=settings.ambulance_max_call_seconds,
    )

    result = await provider.initiate_outbound_call(req)
    if not result.ok:
        booking = ambulance_service.mark_call_failed(
            session, booking, call_status=CallStatus.FAILED, detail=result.detail,
        )
        ambulance_service.enqueue_booking_update(session, booking, "failed")
        logger.warning(f"[ambulance] call failed for {booking.id}: {result.detail}")
        # 200 with the booking so the UI shows the honest CALL_FAILED state + retry.
        return _booking_read(session, booking)

    booking = ambulance_service.attach_provider_call(
        session, booking, provider=result.provider, provider_call_id=result.provider_call_id,
    )
    ambulance_service.enqueue_booking_update(session, booking, "initiated")
    logger.info(
        f"[ambulance] call placed for booking {booking.id} -> {mask_phone(to_number)} "
        f"(provider={result.provider}, mock={provider.is_mock})"
    )
    return _booking_read(session, booking)


@router.post("/bookings/{booking_id}/retry", response_model=BookingRead)
async def retry_call(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    try:
        ambulance_service.retry_call(session, booking)
    except BookingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _booking_read(session, booking)


@router.post("/bookings/{booking_id}/cancel", response_model=BookingRead)
async def cancel_call(
    booking_id: str,
    user_ref: str = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> BookingRead:
    booking = ambulance_service.get_booking_for_user(session, user_ref, booking_id)
    if booking is None:
        raise HTTPException(status_code=404, detail="Booking not found")
    # Best-effort hang up at the provider if a call is live.
    if booking.provider_call_id:
        try:
            await get_telephony_provider().terminate_call(booking.provider_call_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[ambulance] terminate failed for {booking.id}: {exc}")
    booking = ambulance_service.apply_call_status(
        session, booking, CallStatus.CANCELLED, detail="cancelled by user",
    )
    return _booking_read(session, booking)
