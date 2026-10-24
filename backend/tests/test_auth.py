from app.auth import issue_token, verify_token


def test_send_otp_valid(client):
    r = client.post("/api/auth/send-otp", json={"phone": "+91 98765 43210"})
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_send_otp_invalid_phone(client):
    r = client.post("/api/auth/send-otp", json={"phone": "abc"})
    assert r.status_code == 200
    assert "error" in r.json()


def test_verify_otp_correct_issues_token(client):
    r = client.post("/api/auth/verify-otp", json={"phone": "+91 98765 43210", "code": "123456"})
    assert r.status_code == 200
    body = r.json()
    assert body.get("success") is True
    # Phone is normalized to E.164.
    assert body.get("phone") == "+919876543210"
    # A real signed session token is issued and decodes back to the same user.
    assert body.get("token")
    assert verify_token(body["token"]) == "+919876543210"


def test_verify_otp_wrong_code(client):
    r = client.post("/api/auth/verify-otp", json={"phone": "+919876543210", "code": "000000"})
    assert r.status_code == 200
    assert "error" in r.json()
    assert "token" not in r.json()


def test_verify_otp_invalid_phone_rejected(client):
    # Not a valid number per libphonenumber metadata — must be rejected.
    r = client.post("/api/auth/verify-otp", json={"phone": "+911111111111", "code": "123456"})
    assert r.status_code == 200
    assert "error" in r.json()


def test_token_tamper_rejected():
    token = issue_token("+919876543210")
    body, sig = token.split(".")
    tampered = body + "." + ("a" * len(sig))
    import pytest

    from app.auth import TokenError

    with pytest.raises(TokenError):
        verify_token(tampered)
