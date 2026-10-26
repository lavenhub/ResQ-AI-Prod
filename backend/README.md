# ResQAI Backend — AI Emergency Voice Agent

Python backend for ResQAI: a real-time, multilingual **voice** emergency
dispatcher (Pipecat + Google Gemini Multimodal Live over WebRTC) plus the
minimum API the existing frontend needs — demo auth and a validated, persisted
incident model with the tools the agent calls.

> This delivers the **voice agent + its supporting API** and a production-oriented
> **emergency email notification** pipeline (verified contacts, a durable outbox,
> a real transactional provider, and signed delivery webhooks). It does **not**
> dispatch real emergency services, and it never claims an email was delivered or
> read without provider confirmation (see *Honesty & scope*).

---

## Architecture

```
Browser (existing frontend, unchanged)
  │  getUserMedia → RTCPeerConnection + "chat" data channel
  │  POST /api/rtc/offer  (SDP + language + lat/lng/address)
  ▼
FastAPI (this backend, :8001)
  │  SmallWebRTCConnection ← SDP answer
  ▼
Pipecat pipeline (one per call)
  transport.input ─▶ Gemini Live (STT+LLM+TTS, native) ─▶ transport.output
                           │                 ▲
                           ▼                 │ RTVI messages (speaking/transcription)
                     validated tools ────────┘ → "chat" data channel → frontend
    record incident          live retrieval (time-sensitive facts)
      • create_emergency_incident      • get_weather_alerts   → Open-Meteo
      • update_emergency_incident      • find_nearby_hospitals→ OpenStreetMap
          → SQLite (incidents)         • get_disaster_advisories (opt. feed)
      • get_current_location           • get_local_emergency_info (IN reference)
```

**Behaviour vs. facts (key design rule).** The system prompt holds *behaviour* —
triage logic, scenario reasoning, safety do/don'ts, honesty rules, voice style.
*Changing facts* (weather, nearby hospitals, advisories, helpline numbers) are
**retrieved through tools at call time**, never hard-coded in the prompt. This is
why responses stay current: the model is told to look things up rather than
recite stale training knowledge. See `app/voice/prompts.py` (behaviour),
`app/voice/scenarios.py` (scenario catalogue), `app/services/retrieval.py` (live
lookups) and `app/services/emergency_reference.py` (official reference numbers).

**Why Pipecat + Gemini Live (not LiveKit):** the existing frontend already
speaks Pipecat's SmallWebRTC/RTVI protocol (data channel `chat`, `POST
/api/rtc/offer`, RTVI message types), so no frontend rewrite is needed. Gemini
Multimodal Live does speech-to-speech natively with low latency and supports
Hindi (`hi-IN`) and Marathi (`mr-IN`), from a single free API key.

---

## Requirements

- **Python 3.11** (tested on 3.11.9). Avoid 3.14 — some audio deps lag on it.
- A **Google Gemini API key** for live voice (free: <https://aistudio.google.com/apikey>).
  Without it the backend still runs; voice is reported as disabled.

## Setup

```bash
cd backend
py -3.11 -m venv .venv
.venv/Scripts/pip install -r requirements.txt     # Windows
# source .venv/bin/activate && pip install -r requirements.txt   # macOS/Linux

cp .env.example .env          # then edit .env and add GEMINI_API_KEY
```

## Run

```bash
cd backend
.venv/Scripts/python -m uvicorn app.main:app --reload --port 8001
```

For a **fresh production database**, create the schema with Alembic first
(`alembic upgrade head`); migrating an **existing legacy SMS-era** DB is
`python -m scripts.migrate_legacy_sms_db`. In local dev the app also create-alls
the current schema on startup, so neither is required just to run locally.

Then start the frontend (`cd frontend && bun install && bun dev`). The frontend
already proxies `/api` and `/health` to `http://localhost:8001`, so no frontend
config is needed for local dev.

## Test

```bash
cd backend
.venv/Scripts/python -m pytest          # full suite — no network / no API key needed
.venv/Scripts/python scripts/check_pipeline.py   # construct the voice pipeline (dummy key)
.venv/Scripts/python -m scripts.e2e_email_verify # real-socket e2e of the email pipeline (mock sink)
```

The e2e exercises the **real** Resend provider code against a local HTTP sink +
a genuine Svix-signed webhook (no live account needed): add contact → verify →
SOS → `accepted_by_provider` → signed webhook → `delivered`, plus a forged webhook
rejected.

Tests never hit the network: retrieval HTTP is mocked (covering timeouts, rate
limits, malformed responses, stale cache and provider failure), and the scenario
suite asserts the prompt keeps its safety invariants for every scenario family.

---

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | *(empty)* | Google AI Studio key. Empty ⇒ voice disabled (honest). |
| `GEMINI_LIVE_MODEL` | *(service default)* | Override the Gemini Live model id. |
| `GEMINI_VOICE` | `Puck` | Gemini Live voice name. |
| `PORT` | `8001` | Server port (must match the frontend proxy). |
| `CORS_ORIGINS` | localhost 3000/5173 | Comma-separated allowed origins. |
| `DEMO_OTP_CODE` | `123456` | Fixed demo OTP (no SMS is sent). |
| `AUTH_SECRET` | *(ephemeral)* | HMAC secret for signed session tokens. **Set in prod.** |
| `AUTH_TOKEN_TTL_SECONDS` | `604800` | Session token lifetime. |
| `DATABASE_URL` | `sqlite:///./resqai.db` | SQLite now; swap for Postgres later. |
| `EMAIL_PROVIDER` | `mock` | `mock` (dev/tests, sends nothing) / `resend` (real transactional email). |
| `EMAIL_API_KEY` | *(empty)* | Provider API key (Resend `re_…`). Server-only secret. |
| `EMAIL_FROM_ADDRESS` | *(empty)* | Verified sender address (domain must be verified). Required to send. |
| `EMAIL_FROM_NAME` | `ResQAI Alerts` | Sender display name. |
| `EMAIL_REPLY_TO` | *(empty)* | Optional Reply-To. |
| `EMAIL_WEBHOOK_SECRET` | *(empty)* | Provider webhook signing secret (Resend/Svix `whsec_…`). Empty ⇒ webhooks rejected. |
| `EMAIL_API_BASE` | `https://api.resend.com` | Provider API base; override only for staging/test sink. |
| `EMAIL_PROVIDER_TIMEOUT_MS` | `10000` | Per-send HTTP timeout to the provider. |
| `EMAIL_MAX_ATTEMPTS` | `4` | Send attempts per recipient before `FAILED`. |
| `EMAIL_RETRY_BASE_DELAY_MS` | `2000` | Base for exponential backoff. |
| `EMAIL_WORKER_POLL_MS` | `1000` | Outbox worker poll interval. |
| `EMAIL_WORKER_AUTOSTART` | `true` | Start the background worker at boot. |
| `EMAIL_WEBHOOK_TOLERANCE_SECONDS` | `300` | Reject webhooks with a stale signed timestamp (replay guard). |
| `EMAIL_BRAND_NAME` | `ResQAI` | Brand in the email subject/body. |
| `PUBLIC_APP_URL` | `http://localhost:8001` | Public base URL of THIS backend; builds verification links + webhook URL. |
| `EMAIL_VERIFICATION_TTL_SECONDS` | `172800` | Contact email-verification token lifetime (48h). |
| `MAX_CONTACTS_PER_USER` | `10` | Max emergency contacts per user. |
| `SOS_RATE_LIMIT` / `SOS_RATE_WINDOW_SECONDS` | `5` / `300` | SOS rate limit. |
| `CONTACTS_RATE_LIMIT` / `CONTACTS_RATE_WINDOW_SECONDS` | `60` / `60` | Contact-write rate limit. |
| `MAX_SESSION_SECONDS` | `300` | Hard cap per voice session (cost guard). |
| `MAX_CONCURRENT_SESSIONS` | `3` | Reject new calls past this (cost guard). |
| `STORE_TRANSCRIPTS` | `false` | Privacy: transcripts are not persisted by default. |
| `LOG_LEVEL` | `INFO` | Log verbosity. |
| `ENABLE_RETRIEVAL` | `true` | Master switch for live weather/hospital/advisory tools. |
| `DEFAULT_COUNTRY` | `IN` | Jurisdiction for official helpline reference data. |
| `RETRIEVAL_TIMEOUT_SECONDS` | `6` | Hard per-request timeout (voice must not stall). |
| `RETRIEVAL_USER_AGENT` | *(identifying string)* | Required by OSM/Overpass policy. |
| `WEATHER_CACHE_SECONDS` | `600` | Weather cache freshness window. |
| `HOSPITALS_CACHE_SECONDS` | `1800` | Nearby-hospital cache freshness window. |
| `ADVISORY_CACHE_SECONDS` | `120` | Advisory cache freshness window. |
| `OVERPASS_URLS` | 2 mirrors | OpenStreetMap Overpass endpoints (failover). |
| `DISASTER_ADVISORY_URL` | *(empty)* | Optional official advisory feed; empty ⇒ honest "not configured". |
| `TELEPHONY_PROVIDER` | `mock` | `mock` (no real call) / `twilio` (real PSTN) / `exotel` (demo-unverified adapter). |
| `TELEPHONY_ACCOUNT_SID` | *(empty)* | Twilio Account SID (or Exotel SID). Server-only secret. |
| `TELEPHONY_AUTH_TOKEN` | *(empty)* | Twilio Auth Token (or Exotel API token). Server-only secret. |
| `TELEPHONY_CALLER_ID` | *(empty)* | Caller ID the outbound call presents (a provider-owned number), E.164. |
| `TELEPHONY_WEBHOOK_SECRET` | *(empty)* | Empty for Twilio (signed via Auth Token); mock/Exotel use it as a shared secret. |
| `TELEPHONY_BASE_URL` | *(= `PUBLIC_APP_URL`)* | Public https base URL the provider calls back on (TwiML/status/media). Render URL or ngrok. |
| `DEMO_RECEPTION_NUMBER` | *(empty)* | Team-controlled demo reception number (E.164); seeded into the directory. |
| `AMBULANCE_MAX_CALL_SECONDS` | `300` | Hard cap per outbound call (cost guard). |
| `AMBULANCE_MAX_CALL_ATTEMPTS` | `3` | Max call attempts per booking. |
| `AMBULANCE_VOICE` | *(= `GEMINI_VOICE`)* | Gemini Live voice for the ambulance caller. |

Secrets live only on the server (this `.env`). Nothing here is exposed to the
browser.

---

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Liveness. |
| GET | `/api/status` | `{hasApiKey}` — true only when a real key is set. |
| GET | `/api/email/health` | Email provider readiness (never sends). `isMock`/`realSendingConfigured`. |
| POST | `/api/auth/send-otp` | Demo; validates + normalizes phone (E.164). |
| POST | `/api/auth/verify-otp` | Accepts `DEMO_OTP_CODE`; returns `{phone, token}` (signed session). |
| POST | `/api/incidents` | Create. `201` new / `200` idempotent (same `session_id`). |
| GET | `/api/incidents/{id}` | Fetch one. |
| GET | `/api/incidents?limit=&status_filter=` | List. |
| PATCH | `/api/incidents/{id}` | Partial update (never nulls prior fields). |
| POST | `/api/rtc/offer` | WebRTC handshake → SDP answer. `503` if no key. Reads bearer token (optional). |
| GET/POST | `/api/contacts` | **Auth.** List / create the caller's emergency email contacts. |
| PATCH/DELETE | `/api/contacts/{id}` | **Auth.** Update / delete own contact (ownership enforced). |
| POST | `/api/contacts/{id}/send-verification` | **Auth.** (Re)send a single-use verification email to the contact. |
| GET | `/api/contacts/verify?token=…` | **Public.** Single-use email-verification landing page (token is the credential). |
| POST | `/api/emergency/sos` | **Auth.** Create incident + queue email to own verified contacts. Idempotent, rate-limited. |
| GET | `/api/emergency/incidents/{id}` | **Auth.** Incident + per-recipient notification status + summary. |
| GET | `/api/emergency/incidents` | **Auth.** List own incidents. |
| POST | `/api/emergency/incidents/{id}/cancel` | **Auth.** False-alarm cancel; stops not-yet-sent jobs. |
| POST | `/api/emergency/notifications/{id}/retry` | **Auth.** Re-queue a FAILED/UNKNOWN notification. |
| POST | `/api/email/webhook` | Provider delivery-event webhook. Svix-signature auth over raw body; idempotent; `401` if forged. |
| GET/POST | `/api/ambulance/services` | **Auth.** List / add directory entries (dial targets). Numbers returned masked. |
| PATCH/DELETE | `/api/ambulance/services/{id}` | **Auth.** Edit / delete a service. |
| POST | `/api/ambulance/services/{id}/verify` | **Auth.** Mark a service verified (eligible to be called). |
| POST | `/api/ambulance/bookings` | **Auth.** Create a booking from an incident (or auto-create one). |
| GET | `/api/ambulance/bookings/{id}` | **Auth.** Booking + live call/booking state (poll this). |
| GET | `/api/ambulance/bookings?incident_id=` | **Auth.** List own bookings. |
| POST | `/api/ambulance/bookings/{id}/call` | **Auth.** Place the REAL outbound call. Validates consent + number + duplicate-call. |
| POST | `/api/ambulance/bookings/{id}/retry` | **Auth.** Retry a failed call (bounded). |
| POST | `/api/ambulance/bookings/{id}/cancel` | **Auth.** Cancel/hang up a call. |
| POST | `/api/ambulance/telephony/twiml/{id}` | Provider-facing. Signature-verified; returns `<Connect><Stream>` TwiML. |
| POST | `/api/ambulance/telephony/status/{id}` | Provider-facing call-status callback; signature-verified → state machine. |
| WS | `/api/ambulance/telephony/media/{id}` | Provider media stream; runs the Gemini Live ambulance pipeline (real provider only). |

Endpoints marked **Auth** require `Authorization: Bearer <token>` (the token from
`verify-otp`). They operate only on the caller's own data.

**Incident fields** mirror the frontend's `BriefingReport`: `incident_type,
description, reported_symptoms, reported_hazards, latitude, longitude,
location_accuracy, address, detected_language, severity, status, conscious,
breathing, bleeding, created_at, updated_at`. Anything the caller didn't report
stays `null` — never fabricated.

---

## Multilingual support

English, Hindi and Marathi are the targeted/tested-intent languages and map to
`en-IN`, `hi-IN`, `mr-IN`. Other Indian languages the frontend lists are mapped
where Gemini supports them, and otherwise fall back to Hindi (logged, so the
limitation is visible). **Real spoken-language quality must be verified with
actual audio** (see below) — it has not been machine-verified here.

---

## Emergency EMAIL alerting (contacts)

When a signed-in user taps **SOS** (or asks the voice agent to alert contacts),
the backend — never the browser — emails the user's saved, **verified** emergency
contacts via a transactional email provider. Flow:

```
authenticated SOS
  → validate + rate-limit + idempotency
  → resolve recipients from the user's OWN verified, active contacts (never client-supplied addresses)
  → create Incident + one NotificationJob per recipient, in ONE DB transaction  (durable outbox)
  → background worker claims due jobs, sends via the provider, records the outcome
      ACCEPTED_BY_PROVIDER  (provider took it — NOT delivered)
  → provider webhook (Svix-signed)  → DELIVERED | DEFERRED | BOUNCED | FAILED
  → frontend polls incident status and shows each recipient's true state
```

Key properties: per-recipient independence (one bad address never blocks others);
exponential backoff with a max-attempts cap, then dead-letter to `FAILED`; a send
**timeout → `UNKNOWN`, never a blind resend**; a stable per-job **Idempotency-Key**
sent to the provider so even a retried send can't create a duplicate email;
idempotent SOS (same `idempotency_key` ⇒ same incident, no second wave); provider
acceptance recorded **distinctly** from delivery. A hard bounce / spam complaint
**suppresses** the contact so we stop emailing an address that rejects us.

### Statuses (truthful, never conflated)

`queued → processing → accepted_by_provider → delivered` on the happy path, plus
`deferred` (temporary provider delay), `bounced` (mailbox rejected — terminal),
`failed` (permanent/exhausted — terminal), `unknown` (indeterminate, e.g. timeout),
and `cancelled` (false-alarm before send). We never report "opened" or "read" —
delivery is as far as truthful tracking goes, and there are **no tracking pixels**.

### Provider: Resend

All provider calls sit behind one interface (`app/services/email/base.py`), so a
second provider (e.g. SendGrid) can be added as another `EmailProvider` without
touching the pipeline. The implemented real provider is **Resend**
(`app/services/email/resend_provider.py`), chosen over SendGrid for its simple REST
API, clean Svix-signed webhooks, and a free tier (3,000/mo, 100/day) suitable for
testing. The integration uses the verified API: `POST https://api.resend.com/emails`,
`Authorization: Bearer re_…`, body `{from,to,subject,html,text,…}`, success
`{"id":"<uuid>"}`; delivery events arrive as Svix-signed webhooks.

`EMAIL_PROVIDER` selects the implementation:

| `EMAIL_PROVIDER` | Cost | Use |
|---|---|---|
| `mock` (default) | free | dev/tests; sends nothing. Returns the verification link in the API response so the loop can be demoed. |
| `resend` | free tier | real transactional email |

### Email verification (consent)

A contact is eligible for alerts only once it is **active + verified + not suppressed**.
Saving a contact never implies consent. `POST /api/contacts/{id}/send-verification`
mints a cryptographically random, single-use, time-limited token (only its SHA-256
hash is stored) and emails a `{PUBLIC_APP_URL}/api/contacts/verify?token=…` link.
The recipient clicking it marks the contact verified; the token is then consumed.
With the `mock` provider the link is returned as `dev_verification_url` so the demo
works without a mailbox — with a real provider that field is always `null`.

### Production setup (Resend)

1. Create an account at <https://resend.com> (free tier is enough to test).
2. **Add + verify a sending domain** (Domains → Add Domain). Resend shows the
   exact DNS records to create at your DNS host — **use the values Resend displays**
   (do not copy any from here). Typically:
   - an **SPF** `TXT` record (e.g. host `send`, value `v=spf1 include:amazonses.com ~all`),
   - one or more **DKIM** `TXT`/`CNAME` records (Resend provides selector + value),
   - an optional **MX** record for the sending subdomain, and
   - a recommended **DMARC** `TXT` at `_dmarc.yourdomain` (e.g.
     `v=DMARC1; p=none; rua=mailto:dmarc@yourdomain`).
   Prefer a dedicated subdomain (e.g. `alerts.yourdomain.com`) for reputation.
   Wait for the dashboard to show the domain **Verified**.
3. Create an **API key** (`re_…`) → `EMAIL_API_KEY`. Set `EMAIL_FROM_ADDRESS` to an
   address on the verified domain and `EMAIL_FROM_NAME`.
4. Add a **webhook** (Webhooks → Add) pointing at `{PUBLIC_APP_URL}/api/email/webhook`,
   subscribed to `email.sent/delivered/delivery_delayed/bounced/complained/failed`.
   Copy its signing secret (`whsec_…`) → `EMAIL_WEBHOOK_SECRET`. We verify the Svix
   signature over the raw body with a timestamp tolerance (replay guard).
5. Set `EMAIL_PROVIDER=resend` and a real `PUBLIC_APP_URL` (https, publicly reachable).
6. Verify readiness (sends nothing): `GET /api/email/health` → `realSendingConfigured:true`.

> **Status in this repo:** `EMAIL_PROVIDER` defaults to `mock` (sends nothing) so no
> credentials are committed, and no real email has been sent from this repo. The
> **real Resend path is fully implemented and verified end-to-end over real sockets**
> (`scripts/e2e_email_verify.py`: send via the real provider code → `accepted_by_provider`
> → genuine Svix-signed webhook → `delivered`, plus a forged webhook rejected). To go
> live you only add your own Resend API key + verified domain + webhook secret — nothing
> in the code is missing. **Remaining production blockers are external:** a Resend
> account, a verified sending domain (DNS you control), and a public HTTPS URL for the
> webhook.

### Controlled real-provider staging test (operator-gated)

Do this only in staging, only to an address **you** are authorized to use, only
after explicit approval — never against real contacts by default:
1. In a **staging** env set `EMAIL_PROVIDER=resend` + real `EMAIL_API_KEY`,
   verified `EMAIL_FROM_ADDRESS`, `EMAIL_WEBHOOK_SECRET`, and a reachable `PUBLIC_APP_URL`.
2. `GET /api/email/health` must show `realSendingConfigured:true` first.
3. Add ONE contact that is your own authorized test inbox; verify it via the link.
4. Trigger one SOS. Confirm: `accepted_by_provider`, the email arrives, and the
   provider webhook flips it to `delivered`.

### Operations

- **Database migrations (Alembic):** fresh/production databases run
  `alembic upgrade head` (creates `incident`, `emergencycontact`, `notificationjob`,
  `notificationevent`). `alembic check` confirms the migration matches the models.
  For a **legacy SQLite DB** from the old SMS build, run
  `python -m scripts.migrate_legacy_sms_db` — it backs the file up to
  `<db>.pre-email-backup`, preserves all incidents, rebuilds contact/job tables to
  the email schema, and stamps Alembic. (In dev, `init_db` also create-alls the
  current schema on startup for convenience.)
- **Worker:** an in-process asyncio worker (`EMAIL_WORKER_AUTOSTART=true`) drains the
  outbox. For serverless/multi-instance, disable autostart and drive
  `app/services/notifier.py::drain_pending`-style processing from a scheduler/queue;
  the `notificationjob` table is the durable outbox and the worker lease
  (`locked_at`) + stale-lease reclaim make that safe.
- **Health/readiness:** `/health` (liveness), `/api/email/health` (provider config,
  never sends), `/api/status` (voice). None of these send email.
- **Monitor:** provider rejections (`notificationjob.last_error`, `status IN (failed,bounced)`),
  outbox backlog (`status IN (queued,processing)` age), repeated retries
  (`attempts` climbing), `unknown` outcomes needing reconciliation, and the
  `notificationevent` audit log for delivery history.
- **Data retention:** incidents/notifications/events persist for audit. Define a
  retention window and purge old rows per your policy; location is stored only on the
  incident, masked in logs, and never silently IP-derived. We never store medical
  transcripts in alerts.
- **Fallback:** if the provider is unavailable, sends stay `queued`/`unknown` and the
  UI keeps an always-visible "Real emergency? Call 112/108 now" banner — email
  notification never implies help has been dispatched.
- **Troubleshooting:** `401` on the webhook ⇒ bad/missing Svix signature or stale
  timestamp. Sends all `FAILED` with "not configured" ⇒ missing `EMAIL_API_KEY`/
  `EMAIL_FROM_ADDRESS` (check `/api/email/health`). `PERMANENT` ⇒ unverified domain,
  invalid recipient, or bad key (Resend's message is kept on `last_error`). `429` ⇒
  provider rate limit (honored via backoff/Retry-After).

---

## Manual voice test (needs a real key + microphone)

1. Put a real `GEMINI_API_KEY` in `backend/.env`; start the backend.
2. Start the frontend; sign in (OTP `123456`); set your profile.
3. Open **Emergency**, pick a language, tap **Start Live Call**, allow the mic.
4. Say e.g. *"A snake just bit me, I'm in a field."* Expect: a calm spoken reply,
   conservative first-aid guidance, and — in the DB — an incident appears
   (`GET /api/incidents`). Repeat in Hindi / Marathi.
5. Interrupt the agent mid-sentence (barge-in) and confirm it stops and listens.

---

## Real outbound ambulance-coordination calls

ResQAI can place a **real outbound phone call** to a configured hospital /
ambulance reception number and have an AI (the SAME Gemini Live stack as the
browser dispatcher) hold a live two-way conversation with whoever answers:
introduce itself as an automated AI caller, explain the emergency using only
verified details, ask about availability / ambulance type / ETA / charges, and
attempt a booking. The **backend owns the booking state** — the LLM reports what
the receptionist said; it can never mark a booking confirmed or claim a dispatch.

### How it works (architecture)

```
User (Emergency screen)                 Backend                        Twilio                Reception phone
  create booking ───────────▶ /api/ambulance/bookings
  start call ───────────────▶ /bookings/{id}/call ──▶ provider.initiate_outbound_call ─▶ dials ───▶ 📞 rings
                                   (reserve → state=INITIATING_CALL)         │
  poll /bookings/{id} ◀──────  state machine ◀── /telephony/status/{id} ◀── status callbacks (ringing/answered/…)
                                                                            │
                              Gemini Live pipeline ◀── WS /telephony/media/{id} ◀── <Connect><Stream> (TwiML)
                                   AmbulanceCallTools ──▶ record_receptionist_response ──▶ backend-validated state
```

The telephony bridge reuses Pipecat's `TwilioFrameSerializer` + a
`FastAPIWebsocketTransport`, so the existing Gemini Multimodal Live pipeline runs
over the phone call almost unchanged (`app/voice/ambulance_pipeline.py`). A
**provider abstraction** (`app/services/telephony/`) keeps Twilio from being
hard-coded — an Exotel adapter and a **free WebRTC** mode ship through the same
interface.

### FREE mode — WebRTC reception (no carrier, no account, $0) — DEFAULT

If you don't want a telephony account, set `TELEPHONY_PROVIDER=webrtc` (the
default). Instead of dialing a phone number, the teammate acting as reception
opens a **browser page and answers** — the AI coordinator talks to them live over
WebRTC (the same transport as the in-app dispatcher). It's a real two-way AI voice
conversation, driven by the same booking state machine; it just isn't over the
phone network. Needs only `GEMINI_API_KEY`.

Flow:
1. Caller taps **Start Ambulance Coordination** → booking goes **RINGING** and the
   UI shows a **reception link** (`/reception?booking=<id>`) to share with the teammate.
2. Teammate opens the link, sees the incoming emergency details, taps **Answer**,
   allows the mic. (`POST /api/ambulance/reception/offer` runs the pipeline over
   `SmallWebRTCTransport`.)
3. On connect → **CONNECTED**; the AI opens, coordinates, and records the outcome
   through the same backend-owned state machine. The caller screen updates live.

**WebRTC networking (important for cloud):** WebRTC media needs UDP. On a local
network STUN is enough (default). On a cloud host (Render) that blocks WebRTC's
UDP, set a **TURN** relay or the call connects with **no audio** — free TURN tiers
exist (e.g. metered.ca):
```
TURN_URLS=turn:your-turn-host:3478
TURN_USERNAME=...
TURN_CREDENTIAL=...
```
The backend serves these to the browser at `GET /api/ice` and uses them on the
server peer, so both ends agree. For the simplest guaranteed-free demo, run the
backend **locally** with both caller and teammate on the same network (STUN only).

### Provider comparison (why Twilio for the demo)

| | Twilio (demo) | Exotel |
|---|---|---|
| Signup | Instant, self-serve, trial credit | Business KYC (slow) |
| India outbound PSTN | Yes (intl leg; trial caller-ID non-Indian) | Native India + Indian caller-ID/DLT |
| Real-time bidir media | Media Streams (Pipecat's canonical path) | Voicebot streaming applet |
| Demo safety | **Trial only calls VERIFIED numbers** — a built-in allowlist | Account-level |
| Pipecat serializer | `TwilioFrameSerializer` ✅ | `ExotelFrameSerializer` ✅ (adapter shipped, demo-unverified) |

### Twilio setup (one time)

1. Create a Twilio account (free trial): <https://www.twilio.com/try-twilio>.
2. Copy **Account SID** + **Auth Token** from the Console dashboard →
   `TELEPHONY_ACCOUNT_SID`, `TELEPHONY_AUTH_TOKEN`.
3. Get a voice-capable Twilio number → `TELEPHONY_CALLER_ID` (E.164).
4. **Verify the teammate's phone** that will act as the reception desk:
   Console → Phone Numbers → **Verified Caller IDs** → add + confirm it. On a
   trial account Twilio will only call verified numbers — this is the built-in
   demo safety guard.
5. Set `DEMO_RECEPTION_NUMBER` to that verified number and
   `TELEPHONY_PROVIDER=twilio`.
6. Expose the backend publicly so Twilio can reach the webhooks + media socket:
   - **Render**: set `TELEPHONY_BASE_URL=https://<your-service>.onrender.com`.
   - **Local**: `ngrok http 8001`, then `TELEPHONY_BASE_URL=https://<id>.ngrok-free.app`.
   No Twilio dashboard webhook config is needed — the backend passes the TwiML
   (`/api/ambulance/telephony/twiml/{id}`), status-callback
   (`/status/{id}`) and media (`wss …/media/{id}`) URLs per call.
7. Seed the demo directory entry:
   ```
   PYTHONPATH=. .venv/Scripts/python scripts/seed_ambulance_demo.py
   ```

### Run the demonstration

1. `GEMINI_API_KEY` + the Twilio vars above in `backend/.env`; backend public
   (Render or ngrok). Start backend + frontend; sign in.
2. (Optional) In **Profile → Ambulance / Hospital Directory**, confirm the
   **DEMO RECEPTION NUMBER — TEAM CONTROLLED** entry is present (or add one).
3. On **Emergency**, scroll to **Ambulance Coordination**, pick the demo
   reception, tick the consent box, tap **Start Ambulance Coordination**.
4. The teammate's phone rings; they answer and play the receptionist. The AI
   opens by disclosing it's an automated ResQAI caller, explains the emergency,
   and asks about availability / ETA / charges.
5. The panel updates **live** from the real backend state: *Calling reception →
   Receptionist connected → Discussing availability → Booking confirmed / declined
   / Follow-up required*. ETA, charges and a booking reference appear **only** if
   the teammate actually says them.

### Booking state machine (backend-owned)

`PREPARING_REQUEST → VALIDATING_DETAILS → INITIATING_CALL → RINGING → CONNECTED
→ … → REQUESTING_CONFIRMATION → BOOKING_CONFIRMED | BOOKING_DECLINED | CALL_FAILED
| CALL_DISCONNECTED | PENDING_FOLLOW_UP → COMPLETED`. Only valid transitions are
allowed and every one is audited in `callevent`. **`BOOKING_CONFIRMED` is set by
the backend only** when an explicit confirmation is reported *and* it qualifies
(booking accepted or ambulance available) from an allowed state. These are kept
distinct and never conflated: *available ≠ accepted ≠ confirmed ≠ dispatched* —
a phone call never auto-marks a dispatch.

### Safety / honesty guarantees

- Only a directory number that is `active` **and** (`verified` or `is_demo`) is
  ever dialed. Arbitrary numbers from the LLM or client are rejected.
- Consent is required before a call shares patient details.
- Duplicate-call prevention: a second tap while a call is in flight is refused.
- Call retries are bounded (`AMBULANCE_MAX_CALL_ATTEMPTS`).
- Every telephony webhook is signature-verified (Twilio `X-Twilio-Signature`);
  forged/unsigned webhooks are rejected.
- Booking-status emails reuse the existing outbox (no second email service) and
  state only what the backend recorded (queued ≠ delivered; never "dispatched").
- `TELEPHONY_PROVIDER=mock` (the default) places **no real call** — used by all
  automated tests and offline demos.

### Troubleshooting

- *503 "Telephony provider not configured"* → missing Twilio creds / caller ID,
  or `TELEPHONY_BASE_URL` is not https.
- *Call never connects / webhooks silent* → `TELEPHONY_BASE_URL` not publicly
  reachable; verify the ngrok/Render URL and that it's the one in `.env`.
- *Twilio rejects the call (21210/21211)* → on trial, the destination isn't a
  Verified Caller ID — verify the teammate's number.
- *Answers but silence* → `GEMINI_API_KEY` missing/invalid (media socket needs
  voice enabled), or the Twilio number isn't voice-capable.

---

## Real-time retrieval — what is genuinely live

The agent answers time-sensitive questions by calling tools, with honest
provenance attached to every result (`source`, `retrieved_at`, `data_as_of`,
`stale`). Three tiers, never conflated:

| Capability | Status | Source | Notes |
|---|---|---|---|
| Weather + weather hazards | **Live** | Open-Meteo (free, no key) | Hazards are *derived from a forecast*, labelled as not an official warning. |
| Nearby hospitals | **Live (best-effort)** | OpenStreetMap Overpass (free, no key) | Mirrors are flaky; on failure the agent degrades to "dial 108/112". Cannot confirm a hospital is open or has beds. |
| Official emergency numbers | **Reference** | Built-in India directory (112/108/101/100/1098/181/14416/…) | Stable published numbers, labelled `official_reference` — not a live status check. |
| Disaster advisories | **Off by default** | `DISASTER_ADVISORY_URL` if you set one | No free credential-free India-wide feed exists; unset ⇒ the tool says "not configured" instead of inventing alerts. |

On any timeout / rate-limit / malformed response the retrieval layer returns a
clearly-flagged failed (or stale) result and the agent falls back to safe general
guidance — it **never fabricates** weather, a hospital's availability, a road
status, a response time, or an advisory.

## Honesty & scope (read this)

- The agent **records** incidents and can **queue email alerts to the caller's own
  saved, verified contacts** (with consent) via `trigger_sos_alert`. It does **not**
  dispatch ambulances/police/fire or call anyone, and it is instructed to say alerts
  are "queued"/"being sent" — **never "delivered"** unless a provider webhook confirms it.
- "Accepted by provider" ≠ "delivered" ≠ "read". The UI and the agent keep these
  distinct; the SOS API returning `200` only means the incident was recorded and
  jobs queued. We never claim an email was opened or read.
- Retrieval is read-only lookups (see the table above); the agent never claims a
  live fact it didn't actually retrieve.
- No key / provider down ⇒ `/api/status` reports `hasApiKey:false` and
  `/api/rtc/offer` returns `503`. The frontend degrades honestly (manual options)
  rather than faking a call.
- **OTP delivery** is still demo (fixed code, no SMS). The **session** is real: a
  signed HMAC bearer token authorizes every per-user action, and contacts/incidents
  are owned per user and ownership-checked on every read/write.
- The LLM can only call the eight validated tools; it has no direct DB or network
  access, cannot set the caller's location (device-provided), and **cannot supply
  recipients** — email recipients are resolved server-side from the user's verified contacts.

## Known limitations

- Spoken Hindi/Marathi quality is **unverified** without a live mic test.
- Gemini free-tier rate limits apply; `MAX_*` env vars cap usage. Verify current
  limits at the AI Studio dashboard.
- One incident per voice session (idempotent on `session_id`); app SOS is
  idempotent on `idempotency_key`.
- SQLite single-file DB; fine for the demo, swap `DATABASE_URL` for Postgres to
  scale.
- **Real email sending is NOT configured by default** (`EMAIL_PROVIDER=mock`). The
  real Resend path is fully implemented and verified end-to-end over real sockets
  (`scripts/e2e_email_verify.py`); turning it on needs only your own Resend API key,
  a verified sending domain, and a public webhook URL. No real email has been sent
  from this repo. See the email section for the exact external blockers.
- Rate limiting and idempotency are **in-process** (single instance). For multi-
  instance deployments back them with Redis / the DB.
- A contact must **confirm its email** (single-use link) before it can be alerted —
  saving an address never implies consent, and a hard bounce/complaint suppresses it.
- OTP login is demo; a real OTP delivery channel is the obvious next step to make
  login production-grade (independent of emergency email).

## Cost

- Gemini Live has a free tier (no card) suitable for a demo; heavy use can incur
  cost or hit rate limits. Everything else (FastAPI, SQLite, aiortc, Silero VAD)
  is free/open-source and runs locally.
