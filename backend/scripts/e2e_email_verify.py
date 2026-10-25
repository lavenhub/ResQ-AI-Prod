"""End-to-end verification of the emergency EMAIL pipeline over real sockets.

This exercises the REAL ResendEmailProvider code path (no mocks): the backend
sends to a local HTTP "Resend sink" via httpx, and a genuine Svix-signed webhook
is delivered back to /api/email/webhook. It proves the truthful progression:

    add contact -> send verification email (captured by sink) -> recipient clicks
    the real link -> verified -> SOS -> worker sends (accepted_by_provider) ->
    signed delivery webhook -> delivered.  Plus: a forged webhook is rejected.

Run (from backend/):  .venv/Scripts/python -m scripts.e2e_email_verify

Exit code 0 = all checks passed. Uses only the stdlib + httpx (already a dep).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import httpx

WEBHOOK_SECRET_PLAIN = b"0123456789abcdef0123456789abcdef"
WEBHOOK_SECRET = "whsec_" + base64.b64encode(WEBHOOK_SECRET_PLAIN).decode()

_checks: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    _checks.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# ── Local "Resend" sink ────────────────────────────────────────────────────────
class _Sink:
    def __init__(self) -> None:
        self.emails: list[dict] = []
        self._counter = 0

    def handler(self):
        sink = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw or b"{}")
                except ValueError:
                    body = {}
                sink._counter += 1
                mid = f"e2e-{sink._counter}"
                sink.emails.append({"id": mid, **body})
                payload = json.dumps({"id": mid}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return H


def svix_headers(body: bytes, *, ts: int | None = None) -> dict:
    svix_id = "msg_e2e"
    ts = ts or int(time.time())
    signed = b"%s.%s.%s" % (svix_id.encode(), str(ts).encode(), body)
    sig = base64.b64encode(hmac.new(WEBHOOK_SECRET_PLAIN, signed, hashlib.sha256).digest()).decode()
    return {"svix-id": svix_id, "svix-timestamp": str(ts), "svix-signature": f"v1,{sig}"}


def main() -> int:
    backend_port = free_port()
    sink_port = free_port()
    base = f"http://127.0.0.1:{backend_port}"
    tmpdb = os.path.join(tempfile.mkdtemp(prefix="resqai_e2e_"), "e2e.db")

    sink = _Sink()
    httpd = HTTPServer(("127.0.0.1", sink_port), sink.handler())
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    env = {
        **os.environ,
        "EMAIL_PROVIDER": "resend",
        "EMAIL_API_BASE": f"http://127.0.0.1:{sink_port}",
        "EMAIL_API_KEY": "re_e2e_test",
        "EMAIL_FROM_ADDRESS": "alerts@resqai.test",
        "EMAIL_FROM_NAME": "ResQAI Alerts",
        "EMAIL_WEBHOOK_SECRET": WEBHOOK_SECRET,
        "EMAIL_WORKER_AUTOSTART": "true",
        "EMAIL_WORKER_POLL_MS": "200",
        "GEMINI_API_KEY": "",
        "AUTH_SECRET": "e2e-secret",
        "DATABASE_URL": f"sqlite:///{tmpdb}",
        "PUBLIC_APP_URL": base,
    }

    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(backend_port),
         "--log-level", "warning"],
        env=env,
    )
    try:
        # Wait for the backend to come up.
        for _ in range(60):
            try:
                if httpx.get(f"{base}/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("backend did not start"); return 2

        with httpx.Client(base_url=base, timeout=10) as c:
            # health: real provider, ready
            h = c.get("/api/email/health").json()
            check(h["provider"] == "resend" and h["realSendingConfigured"] and not h["isMock"],
                  "email health reports real resend provider, ready")

            # login
            tok = c.post("/api/auth/verify-otp",
                         json={"phone": "+919900000001", "code": "123456"}).json()["token"]
            hdr = {"Authorization": f"Bearer {tok}"}

            # add contact
            cid = c.post("/api/contacts", headers=hdr,
                         json={"name": "E2E Contact", "email": "contact@e2e.test"}).json()["id"]
            check(c.get("/api/contacts", headers=hdr).json()[0]["verified"] is False,
                  "new contact starts UNVERIFIED")

            # SOS before verification must be refused (no eligible contacts)
            pre = c.post("/api/emergency/sos", headers=hdr, json={"incident_type": "Fall"})
            check(pre.status_code == 409, "SOS refused while no contact is verified (409)")

            # send verification -> real email captured by the sink
            sv = c.post(f"/api/contacts/{cid}/send-verification", headers=hdr).json()
            check(sv["sent"] is True and sv["dev_verification_url"] is None,
                  "verification email accepted by provider; no dev link leaked for real provider")
            time.sleep(0.3)
            verify_mail = next((e for e in sink.emails if "contact@e2e.test" in json.dumps(e.get("to"))), None)
            check(verify_mail is not None, "verification email reached the provider sink")
            m = re.search(r"https?://[^\s\"'<>]+/api/contacts/verify\?token=[^\s\"'<>]+",
                          (verify_mail or {}).get("text", "") + (verify_mail or {}).get("html", ""))
            check(m is not None, "verification link present in the email body")

            # recipient clicks the real link
            page = c.get(m.group(0).replace(base, ""))
            check(page.status_code == 200 and "confirmed" in page.text.lower(),
                  "clicking the link confirms the email (verify landing page)")
            check(c.get("/api/contacts", headers=hdr).json()[0]["verified"] is True,
                  "contact is now VERIFIED")

            # trigger SOS
            sos = c.post("/api/emergency/sos", headers=hdr,
                         json={"incident_type": "Road Accident", "victim_name": "Asha",
                               "latitude": 19.07, "longitude": 72.87}).json()
            inc_id = sos["incident_id"]
            check(len(sos["notifications"]) == 1 and sos["notifications"][0]["status"] == "queued",
                  "SOS creates incident + QUEUED job")

            # worker sends -> accepted_by_provider
            mid = None
            for _ in range(40):
                st = c.get(f"/api/emergency/incidents/{inc_id}", headers=hdr).json()
                n = st["notifications"][0]
                if n["status"] == "accepted_by_provider":
                    mid = n["provider_message_id"]
                    break
                time.sleep(0.25)
            check(mid is not None, "worker sent the email -> accepted_by_provider (not delivered)")

            # the emergency email reached the sink with maps link + reference
            emer = next((e for e in sink.emails
                         if "contact@e2e.test" in json.dumps(e.get("to"))
                         and "EMERGENCY ALERT" in (e.get("subject", "").upper())), None)
            check(emer is not None and "google.com/maps" in (emer.get("text", "") + emer.get("html", "")),
                  "emergency email contains the Google Maps location link")

            # forged webhook rejected
            body = json.dumps({"type": "email.delivered", "data": {"email_id": mid}}).encode()
            forged = c.post("/api/email/webhook", content=body,
                            headers={"svix-id": "x", "svix-timestamp": str(int(time.time())),
                                     "svix-signature": "v1,AAAA", "content-type": "application/json"})
            check(forged.status_code == 401, "forged (bad-signature) webhook rejected with 401")

            # genuine signed delivery webhook -> delivered
            good = c.post("/api/email/webhook", content=body,
                          headers={**svix_headers(body), "content-type": "application/json"})
            check(good.status_code == 200 and good.json().get("applied") == 1,
                  "valid Svix-signed delivery webhook accepted")
            final = c.get(f"/api/emergency/incidents/{inc_id}", headers=hdr).json()["notifications"][0]
            check(final["status"] == "delivered" and final["delivered_at"],
                  "status is DELIVERED only after the provider confirmed it")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        httpd.shutdown()

    passed = sum(1 for ok, _ in _checks if ok)
    total = len(_checks)
    print(f"\n{'=' * 48}\nE2E EMAIL: {passed}/{total} checks passed")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
