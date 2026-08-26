import base64
import importlib

from fastapi.testclient import TestClient

from app.config import get_settings


def _basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_production_api_exposes_only_health_without_admin_credentials(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ADMIN_API_USERNAME", "test-admin")
    monkeypatch.setenv("ADMIN_API_PASSWORD", "test-password")
    get_settings.cache_clear()

    import app.main as main

    importlib.reload(main)
    client = TestClient(main.app)

    health = client.get("/health")
    assert health.status_code == 200
    assert health.headers["cache-control"] == "no-store, max-age=0"
    assert client.get("/institutions").status_code == 401
    assert client.post("/validate").status_code == 401
    assert client.get("/deals").status_code == 401
    assert client.get("/admin-downloads/WCDS_dataset_manifest.json").status_code == 401
    assert client.get("/docs").status_code == 404
    assert client.get("/openapi.json").status_code == 404

    authenticated = client.get(
        "/admin-downloads/not-a-real-file.json",
        headers=_basic("test-admin", "test-password"),
    )
    assert authenticated.status_code == 404
    get_settings.cache_clear()
