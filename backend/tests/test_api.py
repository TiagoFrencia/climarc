import pytest
from fastapi.testclient import TestClient
from backend.app.main import app
from backend.app.database import init_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_database():
    init_db()


def test_api_ping():
    resp = client.get("/api/ping")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_api_health():
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "sources" in data
    assert "wrf" in data["sources"]
    assert "metar" in data["sources"]
    assert "database" in data["sources"]


def test_api_weather_endpoint():
    resp = client.get("/api/weather?city=rio_cuarto")
    assert resp.status_code == 200
    data = resp.json()
    assert "city" in data
    assert data["city"]["key"] == "rio_cuarto"
    assert "current" in data
    assert "hourly" in data
    assert "daily" in data
    assert "alerts" in data
    assert "models_active" in data
    assert "confidence" in data
    assert "wrf_available" in data

    # Verify hourly contains models_comparison and wrf_nearby_precip_max_mm
    h0 = data["hourly"][0]
    assert "temperature_c" in h0
    assert "models_comparison" in h0
    assert "wrf_nearby_precip_max_mm" in h0


def test_api_radar_endpoint():
    resp = client.get("/api/radar")
    assert resp.status_code == 200
    data = resp.json()
    assert "radar" in data or "host" in data


def test_api_accuracy_endpoint():
    resp = client.get("/api/accuracy?city=rio_cuarto")
    assert resp.status_code == 200
    data = resp.json()
    assert data["city"] == "rio_cuarto"
    assert "samples_count" in data
    assert "status" in data


def test_cors_headers():
    resp = client.options(
        "/api/weather",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert resp.headers.get("access-control-allow-origin") == "http://localhost:5173"

