def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_reports_voice_disabled_without_key(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    # No GEMINI_API_KEY in tests => honest "voice off".
    assert body["hasApiKey"] is False
    assert body["voiceProvider"] is None


def test_rtc_offer_503_without_key(client):
    # Voice disabled => offer must fail honestly, not fake a session.
    r = client.post("/api/rtc/offer", json={"sdp": "v=0", "type": "offer"})
    assert r.status_code == 503
