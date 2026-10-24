def test_create_minimal_incident(client):
    r = client.post("/api/incidents", json={"incident_type": "Snakebite"})
    assert r.status_code == 201
    body = r.json()
    assert body["incident_type"] == "Snakebite"
    assert body["status"] == "GATHERING"
    assert body["severity"] == "UNKNOWN"
    # Unknown fields are null, never fabricated.
    assert body["latitude"] is None
    assert body["reported_symptoms"] is None
    assert body["id"]


def test_create_is_idempotent_per_session(client):
    payload = {"incident_type": "Fire", "session_id": "sess-abc"}
    r1 = client.post("/api/incidents", json=payload)
    assert r1.status_code == 201
    r2 = client.post("/api/incidents", json=payload)
    # Second call returns the SAME incident (no duplicate) with 200.
    assert r2.status_code == 200
    assert r2.json()["id"] == r1.json()["id"]


def test_get_incident(client):
    created = client.post("/api/incidents", json={"incident_type": "Fall"}).json()
    r = client.get(f"/api/incidents/{created['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == created["id"]


def test_get_missing_incident_404(client):
    assert client.get("/api/incidents/does-not-exist").status_code == 404


def test_partial_update_preserves_other_fields(client):
    created = client.post(
        "/api/incidents",
        json={"incident_type": "Road Accident", "description": "car hit a pole"},
    ).json()
    # Update only severity; description must survive.
    r = client.patch(
        f"/api/incidents/{created['id']}",
        json={"severity": "HIGH", "bleeding": "Yes"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["severity"] == "HIGH"
    assert body["bleeding"] == "Yes"
    assert body["description"] == "car hit a pole"  # preserved
    assert body["incident_type"] == "Road Accident"  # preserved


def test_update_missing_incident_404(client):
    assert client.patch("/api/incidents/nope", json={"severity": "LOW"}).status_code == 404


def test_create_rejects_invalid_latitude(client):
    r = client.post("/api/incidents", json={"incident_type": "Fire", "latitude": 999})
    assert r.status_code == 422
    assert r.json()["error"] == "validation_error"


def test_list_incidents(client):
    client.post("/api/incidents", json={"incident_type": "Choking"})
    r = client.get("/api/incidents?limit=100")
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert any(x["incident_type"] == "Choking" for x in rows)
