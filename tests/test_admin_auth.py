from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.admin_auth import (
    SESSION_COOKIE,
    _serializer,
    create_admin_auth_router,
)
from app.config import Settings


def configured_settings(**overrides):
    values = {
        "admin_google_client_id": "client-id",
        "admin_google_client_secret": "client-secret",
        "admin_google_redirect_uri": "https://polaris.example/auth/callback",
        "admin_emails": "admin@example.com, second@example.com",
        "admin_session_secret": "s" * 48,
        "admin_cookie_secure": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def client_for(settings):
    app = FastAPI()
    app.state.settings = settings
    app.include_router(create_admin_auth_router(settings))
    return TestClient(app, base_url="https://testserver")


def session_cookie(settings, email="admin@example.com", csrf="csrf-token"):
    return _serializer(settings, "polaris-admin-session-v1").dumps(
        {"email": email, "csrf": csrf, "issued_at": 1}
    )


def test_login_is_unavailable_when_auth_is_not_configured():
    settings = Settings(_env_file=None)
    response = client_for(settings).get("/auth/login")
    assert response.status_code == 503
    assert "Location" not in response.headers


def test_admin_session_requires_authentication():
    response = client_for(configured_settings()).get("/admin/session")
    assert response.status_code == 401


def test_allowlisted_signed_session_can_read_admin_session():
    settings = configured_settings()
    client = client_for(settings)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings), secure=True)
    response = client.get("/admin/session")
    assert response.status_code == 200
    assert response.json() == {
        "authenticated": True,
        "email": "admin@example.com",
        "csrf_token": "csrf-token",
        "access": "read_only",
    }


def test_signed_session_for_non_allowlisted_email_is_rejected():
    settings = configured_settings()
    client = client_for(settings)
    client.cookies.set(
        SESSION_COOKIE,
        session_cookie(settings, email="intruder@example.com"),
        secure=True,
    )
    assert client.get("/admin/session").status_code == 401


def test_logout_requires_csrf_for_authenticated_session():
    settings = configured_settings()
    client = client_for(settings)
    client.cookies.set(SESSION_COOKIE, session_cookie(settings), secure=True)
    assert client.post("/auth/logout").status_code == 403
    response = client.post("/auth/logout", headers={"X-CSRF-Token": "csrf-token"})
    assert response.status_code == 204
    assert SESSION_COOKIE in response.headers.get("set-cookie", "")


def test_login_uses_state_nonce_and_pkce_and_sets_flow_cookie():
    settings = configured_settings()
    response = client_for(settings).get("/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"].startswith(
        "https://accounts.google.com/o/oauth2/v2/auth?"
    )
    assert "code_challenge_method=S256" in response.headers["location"]
    assert "nonce=" in response.headers["location"]
    assert "state=" in response.headers["location"]
    assert "polaris_oidc_flow=" in response.headers["set-cookie"]
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "secure" in response.headers["set-cookie"].lower()
