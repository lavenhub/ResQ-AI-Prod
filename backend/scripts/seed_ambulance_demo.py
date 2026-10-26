"""Seed the team-controlled DEMO reception number into the ambulance directory.

    cd backend && PYTHONPATH=. .venv/Scripts/python scripts/seed_ambulance_demo.py

Idempotent: if an entry already exists for DEMO_RECEPTION_NUMBER it is updated in
place rather than duplicated. The entry is clearly labelled
"DEMO RECEPTION NUMBER — TEAM CONTROLLED" and marked ``is_demo=True`` so the UI
flags it and the backend allows it as a call target without full verification
(an unverified, non-demo number is never dialed).
"""

from __future__ import annotations

import sys

from sqlmodel import select

from app.config import get_settings
from app.db import init_db, session_scope
from app.models import AmbulanceService
from app.services.phone import PhoneValidationError, normalize_phone

_DEMO_NAME = "DEMO RECEPTION NUMBER — TEAM CONTROLLED"


def main() -> int:
    settings = get_settings()
    raw = settings.demo_reception_number
    if not raw:
        print(
            "DEMO_RECEPTION_NUMBER is not set. Set it in backend/.env to the REAL "
            "team-controlled phone number that will act as the hospital reception "
            "for the demo (E.164, e.g. +919812345678), then re-run this script."
        )
        return 1
    try:
        e164 = normalize_phone(raw).e164
    except PhoneValidationError as exc:
        print(f"DEMO_RECEPTION_NUMBER is not a valid phone number: {exc}")
        return 1

    init_db()
    with session_scope() as s:
        existing = s.exec(
            select(AmbulanceService).where(AmbulanceService.phone == e164)
        ).first()
        if existing is None:
            existing = s.exec(
                select(AmbulanceService).where(AmbulanceService.name == _DEMO_NAME)
            ).first()
        if existing is None:
            svc = AmbulanceService(
                name=_DEMO_NAME,
                phone=e164,
                city="Demo",
                service_area="Demonstration",
                ambulance_types="Demonstration only",
                capabilities="Team-controlled reception for a live demo call",
                is_demo=True,
                verified=False,
                active=True,
                created_by="seed",
            )
            s.add(svc)
            s.commit()
            s.refresh(svc)
            print(f"[created] {svc.name}  id={svc.id}  phone={e164}")
        else:
            existing.phone = e164
            existing.name = _DEMO_NAME
            existing.is_demo = True
            existing.active = True
            s.add(existing)
            s.commit()
            print(f"[updated] {existing.name}  id={existing.id}  phone={e164}")
    print("Seed complete. This is a DEMONSTRATION entry, not a real emergency service.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
