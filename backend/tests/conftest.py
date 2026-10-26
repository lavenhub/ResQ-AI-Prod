"""Test fixtures.

Env is configured BEFORE importing the app so the lru_cached settings + the DB
engine bind to an isolated temp SQLite file, and voice is DISABLED (no key) so
tests are deterministic and never hit a real provider.
"""

from __future__ import annotations

import os
import pathlib
import tempfile

_TMP = tempfile.mkdtemp(prefix="resqai_test_")
_DB = pathlib.Path(_TMP, "test.db").as_posix()
os.environ["DATABASE_URL"] = f"sqlite:///{_DB}"
os.environ["DEMO_OTP_CODE"] = "123456"
# Voice disabled for deterministic tests. Set to "" (not pop) so that a real key
# in a local backend/.env cannot be re-injected by config's load_dotenv()
# (which only fills vars that are ABSENT) and flip voice on mid-suite.
os.environ["GEMINI_API_KEY"] = ""
# Stable signing secret so issued tokens verify deterministically across the run.
os.environ["AUTH_SECRET"] = "test-auth-secret-not-for-production"
# Mock email provider; drive the worker manually (no background polling in tests).
os.environ["EMAIL_PROVIDER"] = "mock"
os.environ["EMAIL_WORKER_AUTOSTART"] = "false"
# A configured webhook secret so the mock requires the x-webhook-secret header
# (lets us assert that forged/unsigned webhooks are rejected).
os.environ["EMAIL_WEBHOOK_SECRET"] = "test-webhook-secret"
os.environ["EMAIL_FROM_ADDRESS"] = "alerts@resqai.test"
os.environ["EMAIL_FROM_NAME"] = "ResQAI Alerts"
os.environ["PUBLIC_APP_URL"] = "http://testserver"
# Fast, deterministic backoff for retry tests.
os.environ["EMAIL_RETRY_BASE_DELAY_MS"] = "10"
os.environ["EMAIL_MAX_ATTEMPTS"] = "3"
# Telephony: mock provider only — tests NEVER place a real PSTN call. A webhook
# secret is configured so forged/unsigned telephony webhooks can be asserted as
# rejected (mock checks the x-telephony-signature header against it).
os.environ["TELEPHONY_PROVIDER"] = "mock"
os.environ["TELEPHONY_WEBHOOK_SECRET"] = "test-telephony-secret"
os.environ["TELEPHONY_CALLER_ID"] = "+911140000000"
os.environ["AMBULANCE_MAX_CALL_ATTEMPTS"] = "3"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture(scope="session")
def client():
    # Entering TestClient's context runs the app lifespan (init_db, etc.).
    with TestClient(app) as c:
        yield c


def _login(client: TestClient, phone: str) -> dict:
    r = client.post("/api/auth/verify-otp", json={"phone": phone, "code": "123456"})
    body = r.json()
    assert body.get("token"), f"login failed: {body}"
    return body


@pytest.fixture
def auth(client):
    """Return (user_ref, headers) for a freshly-logged-in default user."""
    body = _login(client, "+91 90000 00001")
    return body["phone"], {"Authorization": f"Bearer {body['token']}"}


@pytest.fixture
def auth_other(client):
    """A SECOND distinct user, for cross-user authorization tests."""
    body = _login(client, "+91 90000 00002")
    return body["phone"], {"Authorization": f"Bearer {body['token']}"}


_UNIQUE_COUNTER = {"n": 0}


@pytest.fixture
def unique_user(client):
    """Return (user_ref, headers) for a NEW user with a unique phone, so a test's
    contacts/incidents never collide with another test's state."""
    _UNIQUE_COUNTER["n"] += 1
    # Valid Indian mobile range (starts 9), unique per test within the run.
    phone = f"+9193000{_UNIQUE_COUNTER['n']:05d}"
    body = _login(client, phone)
    return body["phone"], {"Authorization": f"Bearer {body['token']}"}
