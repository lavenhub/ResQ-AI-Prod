"""ResQAI backend entrypoint.

    uvicorn app.main:app --reload --port 8001
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from loguru import logger

from . import __version__
from .config import get_settings
from .db import init_db
from .routes import (
    ambulance,
    auth,
    contacts,
    email_webhook,
    emergency,
    incidents,
    rtc,
    system,
    telephony_webhook,
)
from .services.email import get_provider
from .services.notifier import NotificationWorker
from .services.telephony import get_telephony_provider
from .voice.session import SessionManager


def _configure_logging(level: str) -> None:
    logger.remove()
    logger.add(sys.stderr, level=level.upper())


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    _configure_logging(settings.log_level)
    init_db()
    app.state.session_manager = SessionManager(max_concurrent=settings.max_concurrent_sessions)
    app.state.voice_tasks = set()

    # Surface email provider readiness at startup (actionable, never silent).
    provider = get_provider()
    problems = provider.validate_configuration()
    if provider.is_mock:
        logger.warning(
            "Email provider = MOCK — no real email will be sent. Set EMAIL_PROVIDER=resend "
            "(+ EMAIL_API_KEY, EMAIL_FROM_ADDRESS, EMAIL_WEBHOOK_SECRET) for real delivery."
        )
    elif problems:
        logger.warning(f"Email provider '{provider.name}' NOT ready: {'; '.join(problems)}")
    else:
        logger.info(f"Email provider '{provider.name}' ready for real sending")

    # Surface telephony provider readiness at startup (actionable, never silent).
    tele = get_telephony_provider()
    tele_problems = tele.validate_configuration()
    if tele.is_mock:
        logger.warning(
            "Telephony provider = MOCK — NO real outbound call will be placed. Set "
            "TELEPHONY_PROVIDER=twilio (+ credentials, TELEPHONY_CALLER_ID, "
            "TELEPHONY_BASE_URL) for real ambulance-coordination calls."
        )
    elif tele_problems:
        logger.warning(f"Telephony provider '{tele.name}' NOT ready: {'; '.join(tele_problems)}")
    else:
        logger.info(f"Telephony provider '{tele.name}' ready for real outbound calls")

    # Start the background notification worker (durable outbox processor).
    app.state.notification_worker = NotificationWorker()
    if settings.email_worker_autostart:
        app.state.notification_worker.start()
    else:
        logger.info("Notification worker autostart disabled (EMAIL_WORKER_AUTOSTART=false)")

    logger.info(
        f"ResQAI backend {__version__} ready — voice "
        f"{'ENABLED (gemini-live)' if settings.voice_enabled else 'DISABLED (no GEMINI_API_KEY)'}"
    )
    yield
    # Cancel any in-flight voice sessions on shutdown.
    for task in list(app.state.voice_tasks):
        task.cancel()
    await app.state.notification_worker.stop()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="ResQAI Backend", version=__version__, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_req: Request, exc: RequestValidationError):
        # Consistent, structured 422 the frontend's apiFetch can surface.
        # jsonable_encoder flattens pydantic's ctx (which can hold a raw
        # ValueError) into JSON-safe values.
        return JSONResponse(
            status_code=422,
            content={"error": "validation_error", "detail": jsonable_encoder(exc.errors())},
        )

    @app.exception_handler(Exception)
    async def _unhandled_handler(_req: Request, exc: Exception):
        logger.exception(f"Unhandled error: {exc}")
        return JSONResponse(status_code=500, content={"error": "internal_error"})

    app.include_router(system.router)
    app.include_router(auth.router)
    app.include_router(incidents.router)
    app.include_router(contacts.router)
    app.include_router(emergency.router)
    app.include_router(email_webhook.router)
    app.include_router(rtc.router)
    app.include_router(ambulance.router)
    app.include_router(telephony_webhook.router)
    return app


app = create_app()
