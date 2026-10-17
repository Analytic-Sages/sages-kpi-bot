from fastapi.testclient import TestClient

from app.main import app


def test_admin_control_centre_is_served_with_security_headers():
    response = TestClient(app, base_url="https://testserver").get("/control/")
    assert response.status_code == 200
    assert "Polaris" in response.text
    assert "Continue with Google" in response.text
    assert response.headers["content-security-policy"].find("default-src 'self'") >= 0
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"


def test_admin_control_centre_assets_are_same_origin_and_cache_controlled():
    client = TestClient(app, base_url="https://testserver")
    script = client.get("/control/app.js")
    stylesheet = client.get("/control/app.css")
    assert script.status_code == 200
    assert stylesheet.status_code == 200
    assert '"/api/v1/admin/overview"' in script.text
    assert '"/api/v1/admin/audit-events?"' in script.text
    assert "localStorage" not in script.text
    assert script.headers["cache-control"] == "no-store"
    assert stylesheet.headers["cache-control"] == "no-store"
