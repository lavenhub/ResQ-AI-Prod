# ResQAI — Local demo (no hosting) + same‑WiFi APK

Everything runs on your **laptop**; the phone **APK** connects over the **same WiFi**.
No Render, no Vercel, no telephony, no WebRTC. Code lives on GitHub; that's the only
"hosted" thing. The laptop needs **internet** for Gemini (the AI), Resend (email) and
map tiles — those are external APIs, not your own servers.

## 0. Prerequisites
- Python 3.11 + the backend venv, Node, and a `backend/.env` with:
  - `GEMINI_API_KEY=...` (AI assistant + ambulance coordinator)
  - `EMAIL_PROVIDER=resend` + `EMAIL_API_KEY=...` + `EMAIL_FROM_ADDRESS=onboarding@resend.dev` (real email)
  - `AUTH_SECRET=...`, `DATABASE_URL=sqlite:///./resqai.db`

## 1. Run the backend (laptop)
```
cd backend
.venv/Scripts/python -m uvicorn app.main:app --host 0.0.0.0 --port 8001
```
`--host 0.0.0.0` is required so the phone can reach it over WiFi. The hospital
directory is seeded automatically on first use; log in once as a phone number and
add + verify an emergency contact in Profile (mock/dev link or real email).

## 2A. Laptop‑browser demo (smoothest — full voice both ways)
```
cd frontend && npm run dev     # opens http://localhost:5173
```
Open `http://localhost:5173`, sign in (OTP `123456`). On **Emergency**:
- **Chat with ResQ** — type or tap the mic (speak); replies are read aloud.
- **Trigger Emergency (SOS)** — sends the **real email** to your verified contacts
  and shows a **simulated ambulance** driving in on the map + an "Ambulance booked
  (demo)" card.
- **Coordinate an Ambulance** — pick a hospital → **Call reception (demo)**. The AI
  opens the call, explains the emergency + your coordinates, and asks about
  availability/ETA/charges. **You answer as the receptionist** (type or speak).
  Tap **End call & record booking** → the confirmed ETA/charges/reference show up,
  and the ambulance animates toward you.

## 2B. Same‑WiFi phone APK (Capacitor)
The APK bundles the UI and talks to the laptop backend over the LAN.

1. Find your laptop's LAN IP: `ipconfig` → e.g. `192.168.1.50`.
2. Build the web assets pointing at the laptop (LAN IP, **not** localhost):
   ```
   cd frontend
   # PowerShell:  $env:VITE_API_URL="http://192.168.1.50:8001"; npm run build
   # bash:        VITE_API_URL=http://192.168.1.50:8001 npm run build
   ```
3. In your Capacitor app, allow cleartext HTTP (Android blocks plain http by default):
   `capacitor.config.ts`
   ```ts
   const config: CapacitorConfig = {
     appId: "ai.resq.app",
     appName: "ResQAI",
     webDir: "dist",
     server: { cleartext: true },            // allow http:// to the LAN backend
   };
   ```
   (Also ensure `android:usesCleartextTraffic="true"` in the Android manifest.)
4. `npx cap copy android && npx cap open android` → build/run the APK.
5. On the laptop, **allow inbound port 8001** through Windows Firewall (it usually
   prompts on first run — click Allow). Phone + laptop on the **same WiFi**.

### Phone caveat
Android's WebView usually supports **spoken replies (TTS)** but **not** speech‑to‑text,
so on the phone you **type**; the AI still talks. On the laptop browser both work.

## What's real vs simulated (honest)
- **Real:** the AI conversation (Gemini), the emergency **email** to your verified
  contacts, your GPS/location, the medical‑pass QR.
- **Simulated (clearly labelled "demo"):** the ambulance moving on the map and the
  "reception call" (a real human plays the receptionist; no actual phone call is
  placed). For a real dispatch, call **112 / 108**.
