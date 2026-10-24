# ResQAI Backend — AI Emergency Voice Agent

Python backend for ResQAI: a real-time, multilingual **voice** emergency
dispatcher (Pipecat + Google Gemini Multimodal Live over WebRTC) plus the
minimum API the existing frontend needs — demo auth and a validated, persisted
incident model with the tools the agent calls.

> This phase delivers the **voice agent + its supporting API**. It does **not**
> dispatch real emergency services or notify contacts (see *Honesty & scope*).

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

Then start the frontend (`cd frontend && bun install && bun dev`). The frontend
already proxies `/api` and `/health` to `http://localhost:8001`, so no frontend
config is needed for local dev.

## Test

```bash
cd backend
.venv/Scripts/python -m pytest          # full suite — no network / no API key needed
.venv/Scripts/python scripts/check_pipeline.py   # construct the voice pipeline (dummy key)
```

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
| `SMS_PROVIDER` | `mock` | `mock` (none) / `email` (free SMTP) / `msg91` (paid India SMS). |
| `SMTP_HOST` / `SMTP_PORT` | *(empty)* / `587` | SMTP server (email channel), e.g. `smtp.gmail.com`. |
| `SMTP_USERNAME` / `SMTP_PASSWORD` | *(empty)* | SMTP login (Gmail App Password / Brevo SMTP key). |
| `SMTP_FROM` / `SMTP_FROM_NAME` | *(username)* / `ResQAI Alerts` | From address + display name. |
| `SMTP_USE_TLS` / `SMTP_USE_SSL` | `true` / `false` | STARTTLS (587) vs implicit TLS (465). |
| `MSG91_AUTH_KEY` | *(empty)* | MSG91 auth key (server secret). |
| `MSG91_TEMPLATE_ID` | *(empty)* | DLT-approved Flow/template id (`flow_id`). |
| `MSG91_SENDER_ID` | *(empty)* | Approved 6-char DLT header, e.g. `RESQAI`. |
| `MSG91_DLT_ENTITY_ID` | *(empty)* | DLT Principal Entity id (reference). |
| `SMS_PROVIDER_TIMEOUT_MS` | `8000` | Per-send HTTP timeout to the provider. |
| `SMS_MAX_ATTEMPTS` | `4` | Send attempts per recipient before `FAILED`. |
| `SMS_RETRY_BASE_DELAY_MS` | `2000` | Base for exponential backoff. |
| `SMS_WORKER_POLL_MS` | `1000` | Outbox worker poll interval. |
| `SMS_WORKER_AUTOSTART` | `true` | Start the background worker at boot. |
| `SMS_CALLBACK_SECRET` | *(empty)* | Shared secret authenticating the DLR webhook. |
| `SMS_CALLBACK_URL` | *(empty)* | Public callback URL (ops reference). |
| `SMS_BRAND_NAME` | `RESQAI` | Brand in the SMS body (match DLT template). |
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

Secrets live only on the server (this `.env`). Nothing here is exposed to the
browser.

---

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | Liveness. |
| GET | `/api/status` | `{hasApiKey}` — true only when a real key is set. |
| GET | `/api/sms/health` | SMS provider readiness (never sends). `isMock`/`realSendingConfigured`. |
| POST | `/api/auth/send-otp` | Demo; validates + normalizes phone (E.164). |
| POST | `/api/auth/verify-otp` | Accepts `DEMO_OTP_CODE`; returns `{phone, token}` (signed session). |
| POST | `/api/incidents` | Create. `201` new / `200` idempotent (same `session_id`). |
| GET | `/api/incidents/{id}` | Fetch one. |
| GET | `/api/incidents?limit=&status_filter=` | List. |
| PATCH | `/api/incidents/{id}` | Partial update (never nulls prior fields). |
| POST | `/api/rtc/offer` | WebRTC handshake → SDP answer. `503` if no key. Reads bearer token (optional). |
| GET/POST | `/api/contacts` | **Auth.** List / create the caller's emergency contacts (E.164). |
| PATCH/DELETE | `/api/contacts/{id}` | **Auth.** Update / delete own contact (ownership enforced). |
| POST | `/api/emergency/sos` | **Auth.** Create incident + queue SMS to own active contacts. Idempotent, rate-limited. |
| GET | `/api/emergency/incidents/{id}` | **Auth.** Incident + per-recipient notification status + summary. |
| GET | `/api/emergency/incidents` | **Auth.** List own incidents. |
| POST | `/api/emergency/incidents/{id}/cancel` | **Auth.** False-alarm cancel; stops pending sends. |
| POST | `/api/emergency/notifications/{id}/retry` | **Auth.** Re-queue a FAILED/UNKNOWN notification. |
| POST | `/api/sms/callback?secret=…` | Provider DLR webhook. Shared-secret auth; idempotent; `401` if forged. |

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

## Emergency SMS alerting (contacts)

When a signed-in user taps **SOS** (or asks the voice agent to alert contacts),
the backend — never the browser — sends SMS to the user's saved emergency
contacts. Flow:

```
authenticated SOS
  → validate + rate-limit + idempotency
  → resolve recipients from the user's OWN active contacts (never client-supplied numbers)
  → create Incident + one NotificationJob per recipient, in ONE DB transaction  (durable outbox)
  → background worker claims due jobs, sends via the provider, records the outcome
      PROVIDER_ACCEPTED  (carrier took it — NOT delivered)
  → provider DLR webhook  → DELIVERED | FAILED
  → frontend polls incident status and shows each recipient's true state
```

Key properties: per-recipient independence (one bad number never blocks others);
exponential backoff with a max-attempts cap, then dead-letter to `FAILED`; a send
**timeout → `UNKNOWN`, never a blind resend**; idempotent SOS (same
`idempotency_key` ⇒ same incident, no second wave); provider acceptance recorded
**distinctly** from delivery.

### Channels / providers

`SMS_PROVIDER` picks the channel, all behind one interface
(`app/services/sms/base.py`) so the outbox/worker/status/callback code is shared:

| `SMS_PROVIDER` | Channel | Cost | DLT? | Use |
|---|---|---|---|---|
| `mock` (default) | — | free | no | dev/tests; sends nothing |
| **`email`** | **Email via SMTP** | **free** | **no** | **recommended free real path** |
| `msg91` | SMS (India) | paid + DLT | yes | real SMS once registered |

### Email channel (free, no DLT) — recommended

`SMS_PROVIDER=email` sends the alert by **email over SMTP** — free, reaches any
contact, and needs no telecom registration. Verified end-to-end over a real
socket (provider → worker → `provider_accepted` → delivery webhook → `delivered`).

**Free setup — Gmail (fastest):**
1. On a Google account, enable **2-Step Verification**, then create an **App
   Password** (Google Account → Security → App passwords).
2. In `.env`: `SMS_PROVIDER=email`, `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`,
   `SMTP_USERNAME=you@gmail.com`, `SMTP_PASSWORD=<app password>`,
   `SMTP_FROM=you@gmail.com`, `SMS_CALLBACK_SECRET=<random>`.
3. `GET /api/sms/health` → `{provider:"email", realSendingConfigured:true}`.
4. Add a contact **with an email**, trigger SOS → the contact gets the alert.
   (Gmail gives no delivery webhook, so status stops at `provider_accepted`, which
   is the honest truth — "handed to the mail server".)

**Free setup — Brevo (adds real `delivered` receipts):** use
`SMTP_HOST=smtp-relay.brevo.com` + a Brevo SMTP key, and point Brevo's **event
webhook** at `{SMS_CALLBACK_URL}?secret={SMS_CALLBACK_SECRET}`. We set our own
`Message-ID` on each email and match Brevo's `message-id` event back to the job,
so `delivered`/`hard_bounce` become real per-recipient states. Free tier: 300/day.

Contacts need an email for this channel; a contact without one gets a visible
`FAILED` job ("no email address on file") rather than being silently skipped.

### Provider: MSG91 (India SMS)

For real **SMS** set `SMS_PROVIDER=msg91` + MSG91 credentials. The integration
uses the **verified v5 Flow API**: `POST https://api.msg91.com/api/v5/flow/`,
header `authkey`, body `{flow_id, sender, recipients:[{mobiles, …vars}]}`; success
`{"type":"success","message":"<requestId>"}`. See `app/services/sms/msg91.py`.
**In India this requires paid DLT registration** (there is no free compliant SMS
route to arbitrary numbers — it's TRAI law, not a MSG91 limit).

**One-time MSG91 + DLT setup (India telecom law — not optional, not exempt for
emergencies):**
1. Create a MSG91 account; get your **auth key** (`MSG91_AUTH_KEY`).
2. Complete **DLT registration**: register your Principal Entity, then a **Header/
   Sender ID** (6 chars, e.g. `RESQAI`) and a **Content Template** matching the SMS
   body (see `app/services/sms/templating.py`). Guide:
   https://msg91.com/help/dlt-registration-in-india/step-by-step-guide-to-implement-dlt-in-sms/
3. In MSG91, create a **Flow** from the approved template; its id is
   `MSG91_TEMPLATE_ID` (the v5 `flow_id`). Set `MSG91_SENDER_ID` to the approved header.
4. Configure the **delivery webhook** to `POST {SMS_CALLBACK_URL}?secret={SMS_CALLBACK_SECRET}`.
   MSG91 SMS DLR is not HMAC-signed, so the shared `secret` is how we authenticate it.
5. Verify readiness (no message sent): `GET /api/sms/health` → `realSendingConfigured:true`.

> **Status in this repo:** `SMS_PROVIDER` defaults to `mock` (sends nothing) so
> no credentials are committed. The **free email channel is fully implemented and
> verified end-to-end over a real SMTP socket** — to turn on real delivery you only
> add your own free SMTP creds (Gmail App Password or Brevo) per the steps above;
> nothing in the code is missing. Real **SMS** via MSG91 remains **not configured**
> (needs a paid MSG91 account + DLT approval). No real message has been sent from
> this repo by default.

### Controlled real-SMS staging test (operator-gated)

Do this only in staging, only to a number you are authorized to message, only
after explicit approval — never against real contacts by default:
1. In a **staging** env set `SMS_PROVIDER=msg91` + real credentials + a reachable
   `SMS_CALLBACK_URL`/`SMS_CALLBACK_SECRET`.
2. Create ONE contact that is your own authorized test handset.
3. `GET /api/sms/health` must show `realSendingConfigured:true` first.
4. Trigger one SOS. Confirm: `provider_accepted` on your handset, the SMS arrives,
   and the DLR callback flips it to `delivered`.

### Operations

- **Migrations:** schema is created by SQLModel (`init_db` at startup). The SMS
  feature adds tables `emergencycontact` and `notificationjob` and columns on
  `incident` (`trigger_source, note, idempotency_key, cancelled_at`). On SQLite,
  delete `resqai.db` to recreate in dev; for Postgres use Alembic (add autogenerate
  revisions) and roll back by reverting the revision.
- **Worker:** an in-process asyncio worker (`SMS_WORKER_AUTOSTART=true`) drains the
  outbox. For serverless/multi-instance, disable autostart and drive
  `app/services/notifier.py::drain_pending`-style processing from a scheduler/queue;
  the `notificationjob` table is the durable outbox and the worker lease
  (`locked_at`) + stale-lease reclaim make that safe.
- **Health/readiness:** `/health` (liveness), `/api/sms/health` (provider config,
  never sends), `/api/status` (voice).
- **Monitor:** provider rejections (`notificationjob.last_error`, `status=FAILED`),
  outbox backlog (`status IN (pending,processing)` age), repeated retries
  (`attempts` climbing), and `UNKNOWN` outcomes needing reconciliation.
- **Data retention:** incidents/notifications persist for audit. Define a retention
  window and purge old rows per your policy; location is stored only on the
  incident, masked in logs, and never silently IP-derived.
- **Troubleshooting:** `401` on callback ⇒ wrong/missing `secret`. Sends all
  `FAILED` with "not configured" ⇒ missing MSG91 creds (check `/api/sms/health`).
  `PERMANENT` with a template message ⇒ DLT template/sender mismatch — align the
  body in `templating.py` with the approved template. `429` ⇒ provider rate limit
  (honored via backoff/Retry-After).

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

- The agent **records** incidents and can **queue SMS alerts to the caller's own
  saved contacts** (with consent) via `trigger_sos_alert`. It does **not** dispatch
  ambulances/police/fire or call anyone, and it is instructed to say alerts are
  "queued"/"being sent" — **never "delivered"** unless a DLR callback confirms it.
- "Provider accepted" ≠ "delivered" ≠ "read". The UI and the agent keep these
  distinct; the SOS API returning `200` only means the incident was recorded and
  jobs queued.
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
  phone numbers** — SMS recipients are resolved server-side from the user's contacts.

## Known limitations

- Spoken Hindi/Marathi quality is **unverified** without a live mic test.
- Gemini free-tier rate limits apply; `MAX_*` env vars cap usage. Verify current
  limits at the AI Studio dashboard.
- One incident per voice session (idempotent on `session_id`); app SOS is
  idempotent on `idempotency_key`.
- SQLite single-file DB; fine for the demo, swap `DATABASE_URL` for Postgres to
  scale.
- **Real SMS sending is NOT configured** (no MSG91 account/DLT/template/sender).
  Everything works on the `mock` provider and is verified end-to-end; no real SMS
  has been sent or delivery-confirmed. See the SMS section for the exact blocker.
- Rate limiting and idempotency are **in-process** (single instance). For multi-
  instance deployments back them with Redis / the DB.
- Contacts are stored but **not verified** — saving a number does not prove the
  owner consented or controls it (a confirmation flow is future work).
- OTP is demo; a real SMS-OTP (reusing this same provider layer) is the obvious
  next step to make login production-grade.

## Cost

- Gemini Live has a free tier (no card) suitable for a demo; heavy use can incur
  cost or hit rate limits. Everything else (FastAPI, SQLite, aiortc, Silero VAD)
  is free/open-source and runs locally.
