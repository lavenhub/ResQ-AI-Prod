"""Centralised, environment-driven configuration.

Secrets live ONLY here (loaded from the environment / .env), never in the
frontend. Nothing in this module should ever be imported by client code.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

from dotenv import load_dotenv

# Load backend/.env if present (no-op in production where real env vars are set).
load_dotenv()


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


def _get_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    # ── Server ────────────────────────────────────────────────────────────
    host: str = field(default_factory=lambda: _get("HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _get_int("PORT", 8001))
    # The Vite dev server origins allowed to call this API / open WebRTC.
    cors_origins: list[str] = field(
        default_factory=lambda: _get_list(
            "CORS_ORIGINS",
            [
                "http://localhost:3000",
                "http://localhost:5173",
                "http://127.0.0.1:3000",
                "http://127.0.0.1:5173",
            ],
        )
    )

    # ── Database ──────────────────────────────────────────────────────────
    database_url: str = field(
        default_factory=lambda: _get("DATABASE_URL", "sqlite:///./resqai.db")
    )

    # ── Auth (demo) ───────────────────────────────────────────────────────
    # The frontend advertises a fixed demo OTP. This stays a DEMO until a real
    # SMS provider is wired in a later phase — see README "Known limitations".
    demo_otp_code: str = field(default_factory=lambda: _get("DEMO_OTP_CODE", "123456"))

    # ── Voice / AI provider ───────────────────────────────────────────────
    # Primary real-time pipeline: Google Gemini Multimodal Live (single key,
    # native EN/HI/MR audio). Absent key => voice disabled, honest fallback.
    gemini_api_key: str = field(default_factory=lambda: _get("GEMINI_API_KEY"))
    # Empty => use the Pipecat service's current default live model.
    gemini_live_model: str = field(default_factory=lambda: _get("GEMINI_LIVE_MODEL"))
    gemini_voice: str = field(default_factory=lambda: _get("GEMINI_VOICE", "Puck"))

    # ── Hosted voice agent (Tough Tongue AI) ──────────────────────────────
    # Optional. When set, /api/resqai/config serves this embed URL to the
    # frontend widget at runtime. This is configuration passthrough, NOT a
    # token-minting endpoint — do not treat it as a security boundary.
    resqai_agent_url: str = field(default_factory=lambda: _get("RESQAI_AGENT_URL"))

    # ── Session guardrails (cost / abuse control) ─────────────────────────
    max_session_seconds: int = field(
        default_factory=lambda: _get_int("MAX_SESSION_SECONDS", 300)
    )
    max_concurrent_sessions: int = field(
        default_factory=lambda: _get_int("MAX_CONCURRENT_SESSIONS", 3)
    )

    # ── Live retrieval (weather, nearby hospitals, advisories) ────────────
    # Master switch. When False the retrieval tools stay registered but report
    # "unavailable" honestly instead of calling out — useful for offline demos.
    enable_retrieval: bool = field(
        default_factory=lambda: _get_bool("ENABLE_RETRIEVAL", True)
    )
    # Default jurisdiction for official helpline reference data.
    default_country: str = field(
        default_factory=lambda: _get("DEFAULT_COUNTRY", "IN")
    )
    # Per-request hard timeout (seconds). Kept tight — a voice turn must not
    # stall waiting on a slow third-party API.
    retrieval_timeout_seconds: float = field(
        default_factory=lambda: float(_get_int("RETRIEVAL_TIMEOUT_SECONDS", 6))
    )
    # OSM/Nominatim policy requires an identifying User-Agent.
    retrieval_user_agent: str = field(
        default_factory=lambda: _get(
            "RETRIEVAL_USER_AGENT",
            "ResQAI-Emergency-Assistant/1.0 (+https://github.com/; emergency-demo)",
        )
    )
    # Cache freshness windows (seconds). Short, because emergency facts move.
    weather_cache_seconds: int = field(
        default_factory=lambda: _get_int("WEATHER_CACHE_SECONDS", 600)
    )
    hospitals_cache_seconds: int = field(
        default_factory=lambda: _get_int("HOSPITALS_CACHE_SECONDS", 1800)
    )
    advisory_cache_seconds: int = field(
        default_factory=lambda: _get_int("ADVISORY_CACHE_SECONDS", 120)
    )
    # Overpass (OpenStreetMap) mirrors for nearby-facility lookups. First that
    # answers wins; all flaky, so several are tried then we degrade gracefully.
    overpass_urls: list[str] = field(
        default_factory=lambda: _get_list(
            "OVERPASS_URLS",
            [
                "https://overpass-api.de/api/interpreter",
                "https://overpass.kumi.systems/api/interpreter",
            ],
        )
    )
    # Optional official disaster-advisory feed (CAP/GeoRSS/JSON). No free, stable,
    # public India-wide feed exists without credentials, so this is empty by
    # default: the advisory tool then honestly reports "no live feed configured"
    # instead of fabricating alerts. See README for how to wire NDMA/IMD/Sachet.
    disaster_advisory_url: str = field(
        default_factory=lambda: _get("DISASTER_ADVISORY_URL")
    )

    # ── Auth / sessions ───────────────────────────────────────────────────
    # HMAC secret for signing bearer session tokens issued at OTP verify. MUST be
    # set to a strong random value in production. When empty we generate an
    # ephemeral per-process secret (dev only) and log a warning — tokens then do
    # NOT survive a restart.
    auth_secret: str = field(default_factory=lambda: _get("AUTH_SECRET"))
    # Session token lifetime (seconds). Default 7 days.
    auth_token_ttl_seconds: int = field(
        default_factory=lambda: _get_int("AUTH_TOKEN_TTL_SECONDS", 7 * 24 * 3600)
    )

    # ── SMS provider (emergency alerting) ─────────────────────────────────
    # Which provider implementation to use. "mock" is for dev/tests ONLY and
    # never contacts a real carrier. "msg91" is the real India SMS provider.
    sms_provider: str = field(default_factory=lambda: _get("SMS_PROVIDER", "mock"))
    # MSG91 v5 Flow API credentials (server-only secrets). See README for DLT setup.
    msg91_auth_key: str = field(default_factory=lambda: _get("MSG91_AUTH_KEY"))
    # The DLT-approved Flow/template id (MSG91 calls this flow_id in the v5 API).
    msg91_template_id: str = field(default_factory=lambda: _get("MSG91_TEMPLATE_ID"))
    # The approved 6-char DLT sender/header id (e.g. "RESQAI").
    msg91_sender_id: str = field(default_factory=lambda: _get("MSG91_SENDER_ID"))
    # Optional DLT entity id (PE id). Not sent in the API call, kept for config
    # validation / operator reference.
    msg91_dlt_entity_id: str = field(default_factory=lambda: _get("MSG91_DLT_ENTITY_ID"))
    # Base URL override (lets tests/staging point elsewhere).
    msg91_base_url: str = field(
        default_factory=lambda: _get("MSG91_BASE_URL", "https://api.msg91.com")
    )
    # Per-send HTTP timeout to the provider (ms). A send must not hang a worker.
    sms_provider_timeout_ms: int = field(
        default_factory=lambda: _get_int("SMS_PROVIDER_TIMEOUT_MS", 8000)
    )
    # Max send attempts per recipient before dead-lettering (FAILED).
    sms_max_attempts: int = field(default_factory=lambda: _get_int("SMS_MAX_ATTEMPTS", 4))
    # Base delay for exponential backoff between retries (ms).
    sms_retry_base_delay_ms: int = field(
        default_factory=lambda: _get_int("SMS_RETRY_BASE_DELAY_MS", 2000)
    )
    # How often the background notifier polls the outbox for due jobs (ms).
    sms_worker_poll_ms: int = field(default_factory=lambda: _get_int("SMS_WORKER_POLL_MS", 1000))
    # Start the background worker automatically at app startup. Tests set this
    # False and drive processing deterministically instead.
    sms_worker_autostart: bool = field(
        default_factory=lambda: _get_bool("SMS_WORKER_AUTOSTART", True)
    )
    # Shared secret the provider must present on the delivery-report callback.
    # MSG91 SMS DLR has no HMAC signing, so we require this token in the callback
    # URL and compare it in constant time. Empty => callbacks are REJECTED.
    sms_callback_secret: str = field(default_factory=lambda: _get("SMS_CALLBACK_SECRET"))
    # Public base URL the provider should call back to (for README/config echo).
    sms_callback_url: str = field(default_factory=lambda: _get("SMS_CALLBACK_URL"))
    # Product display name used in the SMS body (must match the DLT template).
    sms_brand_name: str = field(default_factory=lambda: _get("SMS_BRAND_NAME", "RESQAI"))

    # ── Email channel (free, no DLT) — SMTP ───────────────────────────────
    # Works with any SMTP server: a Gmail account + App Password, or a free
    # transactional tier (e.g. Brevo). Used when SMS_PROVIDER=email.
    smtp_host: str = field(default_factory=lambda: _get("SMTP_HOST"))
    smtp_port: int = field(default_factory=lambda: _get_int("SMTP_PORT", 587))
    smtp_username: str = field(default_factory=lambda: _get("SMTP_USERNAME"))
    smtp_password: str = field(default_factory=lambda: _get("SMTP_PASSWORD"))
    # The From: address (for Gmail this must be your own address; for Brevo a
    # verified sender). Falls back to SMTP_USERNAME if unset.
    smtp_from: str = field(default_factory=lambda: _get("SMTP_FROM"))
    smtp_from_name: str = field(default_factory=lambda: _get("SMTP_FROM_NAME", "ResQAI Alerts"))
    # STARTTLS (port 587) by default; set SMTP_USE_SSL=true for implicit TLS (465).
    smtp_use_tls: bool = field(default_factory=lambda: _get_bool("SMTP_USE_TLS", True))
    smtp_use_ssl: bool = field(default_factory=lambda: _get_bool("SMTP_USE_SSL", False))

    # ── Limits (abuse control) ────────────────────────────────────────────
    # Max emergency contacts a single user may store.
    max_contacts_per_user: int = field(
        default_factory=lambda: _get_int("MAX_CONTACTS_PER_USER", 10)
    )
    # Max SOS triggers allowed per user inside the rate window.
    sos_rate_limit: int = field(default_factory=lambda: _get_int("SOS_RATE_LIMIT", 5))
    sos_rate_window_seconds: int = field(
        default_factory=lambda: _get_int("SOS_RATE_WINDOW_SECONDS", 300)
    )
    # Max contact-management writes per user inside the rate window.
    contacts_rate_limit: int = field(
        default_factory=lambda: _get_int("CONTACTS_RATE_LIMIT", 60)
    )
    contacts_rate_window_seconds: int = field(
        default_factory=lambda: _get_int("CONTACTS_RATE_WINDOW_SECONDS", 60)
    )

    # ── Misc ──────────────────────────────────────────────────────────────
    log_level: str = field(default_factory=lambda: _get("LOG_LEVEL", "INFO"))
    # Persist raw transcripts to the incident record? Off by default for privacy
    # (the product rule is to avoid retaining transcripts unless justified).
    store_transcripts: bool = field(
        default_factory=lambda: _get_bool("STORE_TRANSCRIPTS", False)
    )

    @property
    def voice_enabled(self) -> bool:
        """True only when a real AI provider key is configured."""
        return bool(self.gemini_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()
