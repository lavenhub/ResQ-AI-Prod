# Tough Tongue AI voice agent — integration notes

This documents how the hosted **Tough Tongue AI** voice agent (from the
`resqai-agent/` starter) was integrated into the existing ResQAI app, and the
exact steps to configure and test it.

## What was integrated

A hosted, browser-only voice assistant embedded on the **Emergency / SOS screen**
(`frontend/src/routes/emergency.tsx`). It runs entirely inside an iframe served
by Tough Tongue AI, so it works for a demo **even when the Python voice backend
is not running**.

### Important: this did NOT replace the existing SOS flow

The project's original live dispatcher — a custom WebRTC pipeline
(`frontend/src/hooks/useVoiceAgent.ts` → the FastAPI/Pipecat/Gemini backend at
`/api/rtc/offer`) — is **untouched and still the primary voice path**. The
hosted agent is an additional, clearly-labelled card below the existing
"AI Dispatcher" controls. The map, medical pass, transcript, GPS panel, auth,
routing, styling, and build system are all unchanged.

Opening the hosted agent ends any active live dispatcher call first, so the two
voice paths never fight over the microphone.

## Files changed / added

**Frontend**
- `src/components/ResQAIWidget.tsx` — **new.** Adapted from the starter's
  `frontend/ResQAIWidget.tsx`: ported to the project's neo-brutalist Tailwind
  (no separate CSS file), Vite env syntax, lucide icons, `@/lib/api`, a portal
  modal with Escape-to-close / focus handling / `role="dialog"` + `aria-modal`,
  and not-configured / load-error / loading states.
- `src/routes/emergency.tsx` — import + one new card rendering `<ResQAIWidget>`.
- `.env.example` — added `VITE_RESQAI_AGENT_URL` and `VITE_RESQAI_SCENARIO_ID`.
- `.gitignore` — now ignores `.env` / `.env.*` (keeps `.env.example`) so
  token-bearing embed URLs can't be committed.

**Backend** (FastAPI — the existing server; no second server was added)
- `app/routes/resqai.py` — **new.** `GET /api/resqai/config` returns the
  server-configured embed URL (or `503` when unset) with `Cache-Control: no-store`.
- `app/config.py` — added the `resqai_agent_url` setting (`RESQAI_AGENT_URL`).
- `app/main.py` — registers the new router.
- `.env.example` — added `RESQAI_AGENT_URL`.
- `tests/test_resqai.py` — **new.** Covers configured + unconfigured responses.
- `tests/conftest.py` — hardened so a real `GEMINI_API_KEY` in a local `.env`
  can no longer leak into the test process (see "Test note" below).

## Configuration

The widget resolves its embed URL in this order (most explicit wins):

1. `agentUrl` prop
2. `VITE_RESQAI_AGENT_URL` — a full embed URL (use for private/token scenarios)
3. `VITE_RESQAI_SCENARIO_ID` — a scenario id, built into the documented URL
4. backend `GET /api/resqai/config` (only consulted when 1–3 are unset)
5. the **public demo scenario** baked into the component
   (`6ad83140767d01fd14f81fa4`, from the starter README)

Because of step 5, **the agent works with zero configuration** for the demo.

### To point at your own / a private scenario

Create `frontend/.env.local` (gitignored) with either:

```
# full embed URL (for a private/passcode/token scenario)
VITE_RESQAI_AGENT_URL=https://app.toughtongueai.com/embed/<scenarioId>?bg=black&skipPrecheck=true
# ...or just the id:
VITE_RESQAI_SCENARIO_ID=<scenarioId>
```

Get the current URL from the Tough Tongue AI dashboard → your scenario →
**Integrate** → copy the iframe `src`. Restart `vite dev` after changing env.

### Optional: rotate the URL without rebuilding the frontend

Set `RESQAI_AGENT_URL` in `backend/.env` and leave the `VITE_RESQAI_*` vars
empty. The widget then fetches it at runtime from `/api/resqai/config`.

## Run

```
# frontend
cd frontend && bun install && bun run dev        # http://localhost:5173

# backend (optional for the hosted agent; required for the live dispatcher)
cd backend && .venv/Scripts/python -m uvicorn app.main:app --reload --port 8001
```

## Test

Automated (all green):

```
cd frontend && ./node_modules/.bin/tsc --noEmit    # typecheck
cd frontend && ./node_modules/.bin/vite build      # production build
cd backend  && .venv/Scripts/python -m pytest      # 23 passed
```

Manual (needs a browser + microphone — not verifiable headlessly):

1. Sign in (demo: any phone, OTP `123456`), open the SOS / Emergency screen.
2. In **Hosted Voice Assistant**, click **Talk to ResQ AI** → the modal opens.
3. Grant microphone when the embedded Tough Tongue UI asks (permission is
   requested inside the provider's iframe, not by this app).
4. Press **Escape** or the ✕ → the modal closes and focus returns to the button.
5. Clear the env + default (pass `scenarioId=""`) to see the "not configured"
   message instead of a blank iframe; use an invalid URL to see the error state.

## Security

- A Tough Tongue embed URL that carries a **private** scenario access token is a
  bearer credential — keep it in `.env.local` / `backend/.env` (both gitignored),
  keep it short-lived, and never commit it.
- The baked-in default scenario id is **public** (it ships in the starter README)
  and is not a secret.
- `VITE_*` values are visible in the browser bundle — never put a permanent
  provider API key there. `/api/resqai/config` is configuration passthrough, not
  a token-minting endpoint.
- The widget does not log transcripts, location, or credentials.

## Honest limitations (not implemented / not verified)

- The hosted agent is a **conversation assistant only**. It does **not** dispatch
  an ambulance/police/fire, email or notify contacts, verify GPS, or call
  emergency services (those belong to the backend pipeline + the primary live
  dispatcher). The modal shows a visible disclaimer saying so.
- The emergency screen's ambulance/vehicle "dispatch" animation and the
  "Alert sent" flow belong to the **pre-existing** app and are simulated /
  config-dependent (EmailJS); this integration did not change or validate them.
- Live spoken conversation quality and the provider's microphone/autoplay
  behavior must be verified manually in a real browser — it can't be exercised
  in this headless environment.

## Test note (why `conftest.py` changed)

The backend test suite asserts the honest "no AI key ⇒ voice disabled" fallback.
`conftest.py` previously `pop`ped `GEMINI_API_KEY`, but `config.load_dotenv()`
then re-read a real key from a local `backend/.env`, flipping voice on and
failing two tests. Setting the var to `""` (present-but-empty) stops
`load_dotenv` from re-injecting it, so the suite is deterministic whether or not
a real key is present locally. No production code path changed.
