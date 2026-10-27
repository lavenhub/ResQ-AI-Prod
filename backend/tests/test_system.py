def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_status_reports_assistant_disabled_without_key(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    body = r.json()
    # No GEMINI_API_KEY in tests => honest "assistant off".
    assert body["hasApiKey"] is False
    assert body["agentModel"] is None
