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
    # The frontend advertises a fixed demo OTP. Login remains a DEMO until a real
    # OTP delivery channel is wired — see README "Known limitations". This is the
    # user's own sign-in and is independent of emergency email notifications.
    demo_otp_code: str = field(default_factory=lambda: _get("DEMO_OTP_CODE", "123456"))

    # ── Voice / AI provider ───────────────────────────────────────────────
    # Primary real-time pipeline: Google Gemini Multimodal Live (single key,
    # native EN/HI/MR audio). Absent key => voice disabled, honest fallback.
    gemini_api_key: str = field(default_factory=lambda: _get("GEMINI_API_KEY"))
    # Empty => use the Pipecat service's current default live model.
    gemini_live_model: str = field(default_factory=lambda: _get("GEMINI_LIVE_MODEL"))
    gemini_voice: str = field(default_factory=lambda: _get("GEMINI_VOICE", "Puck"))

    # ── Conversational text agent (HTTP; works on any host, incl. Render) ──
    # The hosted assistant talks over a normal request/response Gemini call (no
    # WebRTC). Model is overridable; defaults to a fast, fluent flash model.
    # Default to a fast, non-"thinking" flash-lite model: snappy (~1.5s) and it
    # returns COMPLETE replies (the "thinking" flash models burn the output
    # budget on hidden reasoning and truncate). Override with AGENT_MODEL.
    agent_model: str = field(
        default_factory=lambda: _get("AGENT_MODEL") or _get("LLM_MODEL") or "gemini-flash-lite-latest"
    )
    agent_timeout_seconds: float = field(
        default_factory=lambda: float(_get_int("AGENT_TIMEOUT_SECONDS", 30))
    )
    agent_max_output_tokens: int = field(
        default_factory=lambda: _get_int("AGENT_MAX_OUTPUT_TOKENS", 600)
    )

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

    # ── Email provider (emergency alerting via transactional email) ───────
    # Which provider implementation to use:
    #   "mock"   — dev/tests ONLY; never sends a real email.
    #   "resend" — the real transactional email provider (https://resend.com).
    # A provider abstraction (app/services/email) lets a second provider be added
    # later without touching the pipeline.
    email_provider: str = field(default_factory=lambda: _get("EMAIL_PROVIDER", "mock"))
    # Server-only API key for the provider (Resend: "re_..."). NEVER sent to the
    # frontend. Absent => provider reports "not configured" (we never fake a send).
    email_api_key: str = field(default_factory=lambda: _get("EMAIL_API_KEY"))
    # The verified sender address + display name. For a real provider the domain
    # must be verified (SPF/DKIM) — see README. From address is REQUIRED to send.
    email_from_address: str = field(default_factory=lambda: _get("EMAIL_FROM_ADDRESS"))
    email_from_name: str = field(
        default_factory=lambda: _get("EMAIL_FROM_NAME", "ResQAI Alerts")
    )
    # Optional Reply-To (e.g. a monitored inbox). Empty => no Reply-To header.
    email_reply_to: str = field(default_factory=lambda: _get("EMAIL_REPLY_TO"))
    # Webhook signing secret from the provider (Resend/Svix: "whsec_..."). Used to
    # verify inbound delivery-event webhooks. Empty => webhooks are REJECTED.
    email_webhook_secret: str = field(default_factory=lambda: _get("EMAIL_WEBHOOK_SECRET"))
    # Provider API base URL. Override only for a staging proxy or local test sink;
    # defaults to Resend's production API.
    email_api_base: str = field(
        default_factory=lambda: _get("EMAIL_API_BASE", "https://api.resend.com")
    )
    # Per-send HTTP timeout to the provider (ms). A send must not hang a worker.
    email_provider_timeout_ms: int = field(
        default_factory=lambda: _get_int("EMAIL_PROVIDER_TIMEOUT_MS", 10000)
    )
    # Max send attempts per recipient before dead-lettering (FAILED).
    email_max_attempts: int = field(default_factory=lambda: _get_int("EMAIL_MAX_ATTEMPTS", 4))
    # Base delay for exponential backoff between retries (ms).
    email_retry_base_delay_ms: int = field(
        default_factory=lambda: _get_int("EMAIL_RETRY_BASE_DELAY_MS", 2000)
    )
    # How often the background notifier polls the outbox for due jobs (ms).
    email_worker_poll_ms: int = field(
        default_factory=lambda: _get_int("EMAIL_WORKER_POLL_MS", 1000)
    )
    # Start the background worker automatically at app startup. Tests set this
    # False and drive processing deterministically instead.
    email_worker_autostart: bool = field(
        default_factory=lambda: _get_bool("EMAIL_WORKER_AUTOSTART", True)
    )
    # Reject webhook requests whose signed timestamp is older than this (seconds),
    # to blunt replay attacks. Default 5 minutes (the Svix convention).
    email_webhook_tolerance_seconds: int = field(
        default_factory=lambda: _get_int("EMAIL_WEBHOOK_TOLERANCE_SECONDS", 300)
    )
    # Product display name used in the email subject/body.
    email_brand_name: str = field(default_factory=lambda: _get("EMAIL_BRAND_NAME", "ResQAI"))

    # ── Public app URL (for links inside emails) ──────────────────────────
    # The externally-reachable base URL of THIS backend, used to build the
    # single-use contact verification link and (documentation) the webhook URL.
    # Must be https and publicly reachable in production.
    public_app_url: str = field(
        default_factory=lambda: _get("PUBLIC_APP_URL", "http://localhost:8001")
    )
    # Lifetime of a contact email-verification token (seconds). Default 48h.
    email_verification_ttl_seconds: int = field(
        default_factory=lambda: _get_int("EMAIL_VERIFICATION_TTL_SECONDS", 48 * 3600)
    )

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

    # ── Telephony (real outbound ambulance-coordination calls) ────────────
    # Which provider implementation the telephony abstraction uses:
    #   "mock"   — dev/tests ONLY; simulates a call lifecycle, never dials PSTN.
    #   "twilio" — real outbound PSTN via Twilio Programmable Voice + Media Streams.
    #   "exotel" — real outbound PSTN via Exotel (India-native); adapter shipped
    #              but demo-unverified (see services/telephony/exotel_provider.py).
    # A provider abstraction (app/services/telephony) keeps the provider from
    # being hard-coded, so another can be added without touching the pipeline.
    telephony_provider: str = field(
        default_factory=lambda: _get("TELEPHONY_PROVIDER", "mock")
    )
    # Server-only account identity + secret. Twilio: Account SID ("AC...") +
    # Auth Token. NEVER sent to the frontend. Absent => provider reports "not
    # configured" (we never fake a real call). ``telephony_api_key`` /
    # ``telephony_api_secret`` are generic aliases Exotel uses.
    telephony_account_sid: str = field(
        default_factory=lambda: _get("TELEPHONY_ACCOUNT_SID") or _get("TELEPHONY_API_KEY")
    )
    telephony_auth_token: str = field(
        default_factory=lambda: _get("TELEPHONY_AUTH_TOKEN") or _get("TELEPHONY_API_SECRET")
    )
    # The caller ID / "from" number the outbound call presents. For Twilio this
    # MUST be a Twilio number you own (or a verified caller ID). E.164 format.
    telephony_caller_id: str = field(default_factory=lambda: _get("TELEPHONY_CALLER_ID"))
    # Optional dedicated webhook signing secret. Twilio signs status callbacks
    # with the Auth Token (X-Twilio-Signature), so this is usually left empty for
    # Twilio and we verify with the Auth Token; kept for provider-abstraction
    # parity and for providers that use a separate secret.
    telephony_webhook_secret: str = field(
        default_factory=lambda: _get("TELEPHONY_WEBHOOK_SECRET")
    )
    # The externally-reachable PUBLIC base URL of THIS backend, used to build the
    # TwiML, status-callback and media-stream (wss) URLs the provider calls back
    # on. MUST be https/wss and publicly reachable (Render, or ngrok locally).
    # Empty => falls back to PUBLIC_APP_URL. The media WebSocket URL is derived by
    # switching the scheme to ws/wss.
    telephony_base_url: str = field(
        default_factory=lambda: _get("TELEPHONY_BASE_URL") or _get("PUBLIC_APP_URL", "http://localhost:8001")
    )
    # The single demonstration reception number dialed for the demo — a REAL
    # number controlled by the team. The seed script labels its directory entry
    # "DEMO RECEPTION NUMBER — TEAM CONTROLLED". E.164 format, e.g. +919812345678.
    demo_reception_number: str = field(
        default_factory=lambda: _get("DEMO_RECEPTION_NUMBER")
    )
    # Hard cap on a single outbound call's length (seconds) — cost/abuse guard,
    # mirrors MAX_SESSION_SECONDS for the browser dispatcher.
    ambulance_max_call_seconds: int = field(
        default_factory=lambda: _get_int("AMBULANCE_MAX_CALL_SECONDS", 300)
    )
    # Max outbound-call attempts per booking before it must be handled manually.
    ambulance_max_call_attempts: int = field(
        default_factory=lambda: _get_int("AMBULANCE_MAX_CALL_ATTEMPTS", 3)
    )
    # Gemini Live voice used specifically for the outbound ambulance caller.
    # Defaults to the dispatcher voice when unset.
    ambulance_voice: str = field(
        default_factory=lambda: _get("AMBULANCE_VOICE") or _get("GEMINI_VOICE", "Puck")
    )

    # ── WebRTC ICE / TURN (for the browser voice + reception calls) ───────
    # STUN is enough for local / same-network demos. For a CLOUD deploy (e.g.
    # Render) where the server sits behind a proxy that blocks WebRTC's UDP,
    # a TURN relay is needed or the call connects but has NO audio. Set
    # TURN_URLS (+ TURN_USERNAME / TURN_CREDENTIAL) to a TURN server — several
    # providers (e.g. metered.ca) have a free tier. Default: STUN only.
    stun_urls: list[str] = field(
        default_factory=lambda: _get_list("STUN_URLS", ["stun:stun.l.google.com:19302"])
    )
    turn_urls: list[str] = field(default_factory=lambda: _get_list("TURN_URLS", []))
    turn_username: str = field(default_factory=lambda: _get("TURN_USERNAME"))
    turn_credential: str = field(default_factory=lambda: _get("TURN_CREDENTIAL"))
    # Cloudflare TURN (recommended for cloud): set these two and the backend MINTS
    # short-lived TURN credentials on demand (for both the browser and the server
    # peer), so there is nothing to copy or expire. Get them from the Cloudflare
    # dashboard: Realtime → TURN → create a key → Token ID + API Token.
    cloudflare_turn_token_id: str = field(
        default_factory=lambda: _get("CLOUDFLARE_TURN_TOKEN_ID") or _get("CLOUDFLARE_TURN_KEY_ID")
    )
    cloudflare_turn_api_token: str = field(
        default_factory=lambda: _get("CLOUDFLARE_TURN_API_TOKEN")
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

    @property
    def telephony_is_mock(self) -> bool:
        return (self.telephony_provider or "mock").strip().lower() == "mock"

    def media_ws_base(self) -> str:
        """The ws/wss base for the media-stream WebSocket, derived from the
        public HTTP base URL (http->ws, https->wss)."""
        base = (self.telephony_base_url or "").rstrip("/")
        if base.startswith("https://"):
            return "wss://" + base[len("https://"):]
        if base.startswith("http://"):
            return "ws://" + base[len("http://"):]
        return base


@lru_cache
def get_settings() -> Settings:
    return Settings()
