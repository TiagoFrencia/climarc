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

    # ClimaRC v2.1 fields verification
    assert "summary_one_liner" in data
    assert isinstance(data["summary_one_liner"], str)
    assert len(data["summary_one_liner"]) <= 120

    assert "sentinels" in data
    assert isinstance(data["sentinels"], list)
    assert len(data["sentinels"]) == 4
    sentinel_icaos = [s["icao"] for s in data["sentinels"]]
    assert set(sentinel_icaos) == {"SAOC", "SACO", "SAOR", "SAOL"}

    assert "sentinel_front" in data
    assert "detected" in data["sentinel_front"]

    assert "hail_risk" in data
    hail = data["hail_risk"]
    assert "level" in hail
    assert hail["level"] in ("sin_riesgo", "moderado", "alto", "extremo")
    assert "score" in hail
    assert "peak_window_local" in hail
    assert "cape_max" in hail
    assert "shear_0_6km_kmh" in hail
    assert "freezing_level_m" in hail
    assert "goes_severe_core_detected" in hail
    assert "sentinel_hail_detected" in hail
    assert "explanation" in hail

    # Current block v2.1 fields
    curr = data["current"]
    assert "qnh_trend" in curr
    assert curr["qnh_trend"] in ("subiendo", "bajando", "bajando_rapido", "estable")
    assert "qnh_delta_3h_hpa" in curr
    assert "uv_index" in curr

    # Daily v2.1 fields
    d0 = data["daily"][0]
    assert "sunrise_local" in d0
    assert "sunset_local" in d0

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
    assert "satellite" in data
    assert "infrared" in data["satellite"]
    if "lightning" in data:
        assert data["lightning"]["provider"] == "realearth_glm"
        assert "tile_url_template" in data["lightning"]


def test_api_accuracy_endpoint():
    resp = client.get("/api/accuracy?city=rio_cuarto")
    assert resp.status_code == 200
    data = resp.json()
    assert data["city"] == "rio_cuarto"
    assert "samples_count" in data
    assert "status" in data


def test_api_route_weather_endpoint():
    resp = client.get("/api/route-weather?origin=rio_cuarto&destination=cordoba")
    assert resp.status_code == 200
    data = resp.json()
    assert data["origin"] == "rio_cuarto"
    assert data["destination"] == "cordoba"
    assert data["global_status"] in ("optimo", "precaucion", "peligroso")
    assert "max_rain_mm" in data
    assert "max_gust_kmh" in data
    assert "summary" in data
    assert len(data["segments"]) == 3


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

