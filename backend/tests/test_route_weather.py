"""
ClimaRC v2.2 - Unit Tests for Route Weather & Road Mobility Service
"""

import pytest
from backend.app.services.route_service import (
    get_route_weather,
    _evaluate_segment,
    ROUTE_PRESETS,
    ROUTE_ORIGIN,
)


def test_route_presets_definitions():
    """Validates that all 5 requested presets exist and have valid coordinates."""
    required = ["cordoba", "villa_maria", "villa_mercedes", "santa_rosa_calamuchita", "san_luis"]
    for key in required:
        assert key in ROUTE_PRESETS
        preset = ROUTE_PRESETS[key]
        assert -35.0 < preset["lat"] < -30.0
        assert -67.0 < preset["lon"] < -62.0
        assert preset["name"]
        assert preset["route_name"]
        assert preset["midpoint_name"]


def test_segment_evaluation_logic():
    """Validates safety status assignment for individual road segments."""
    # 1. Optimal conditions
    clean_data = {
        "current": {"temperature_2m": 22.0, "apparent_temperature": 22.0, "precipitation": 0.0, "weather_code": 0, "wind_speed_10m": 12.0, "wind_gusts_10m": 18.0, "wind_direction_10m": 90.0},
        "hourly": {"precipitation": [0.0, 0.0, 0.0], "precipitation_probability": [0, 0, 0], "wind_gusts_10m": [15.0, 18.0, 20.0], "visibility": [10000.0, 10000.0, 10000.0]},
    }
    seg_clean = _evaluate_segment("test", "Segmento Test", -32.0, -64.0, clean_data)
    assert seg_clean["status"] == "optimo"
    assert seg_clean["rain_mm"] == 0.0
    assert seg_clean["wind_gust_kmh"] == 20.0

    # 2. Precaution conditions (light rain and moderate gusts)
    precaution_data = {
        "current": {"temperature_2m": 18.0, "apparent_temperature": 17.5, "precipitation": 1.2, "weather_code": 61, "wind_speed_10m": 25.0, "wind_gusts_10m": 42.0, "wind_direction_10m": 180.0},
        "hourly": {"precipitation": [1.5, 0.8, 0.0], "precipitation_probability": [60, 40, 10], "wind_gusts_10m": [45.0, 42.0, 30.0], "visibility": [6000.0, 7000.0, 8000.0]},
    }
    seg_prec = _evaluate_segment("midpoint", "Tramo Medio", -32.5, -64.2, precaution_data)
    assert seg_prec["status"] == "precaucion"
    assert seg_prec["rain_mm"] == 1.5
    assert seg_prec["wind_gust_kmh"] == 45.0

    # 3. Dangerous conditions (thunderstorm + severe crosswind gusts)
    danger_data = {
        "current": {"temperature_2m": 19.0, "apparent_temperature": 19.0, "precipitation": 14.0, "weather_code": 95, "wind_speed_10m": 45.0, "wind_gusts_10m": 72.0, "wind_direction_10m": 210.0},
        "hourly": {"precipitation": [18.0, 5.0, 0.0], "precipitation_probability": [90, 70, 20], "wind_gusts_10m": [75.0, 60.0, 40.0], "visibility": [800.0, 1500.0, 4000.0]},
    }
    seg_danger = _evaluate_segment("destination", "Destino", -31.4, -64.2, danger_data)
    assert seg_danger["status"] == "peligroso"
    assert seg_danger["rain_mm"] == 18.0
    assert seg_danger["wind_gust_kmh"] == 75.0


@pytest.mark.anyio
async def test_get_route_weather_with_mocked_client():
    """Tests get_route_weather end-to-end with simulated responses for 3 points."""
    import httpx

    def mock_handler(request: httpx.Request):
        # Return clean mock weather for each coordinate requested
        lat = float(request.url.params.get("latitude", -33.0))
        return httpx.Response(
            200,
            json={
                "current": {
                    "temperature_2m": 24.5,
                    "apparent_temperature": 24.0,
                    "precipitation": 0.0,
                    "weather_code": 1,
                    "wind_speed_10m": 15.0,
                    "wind_gusts_10m": 25.0,
                    "wind_direction_10m": 45.0,
                },
                "hourly": {
                    "precipitation": [0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
                    "precipitation_probability": [5, 5, 5, 0, 0, 0],
                    "wind_speed_10m": [15.0, 16.0, 14.0, 12.0, 10.0, 10.0],
                    "wind_gusts_10m": [25.0, 28.0, 24.0, 20.0, 18.0, 15.0],
                    "weather_code": [1, 1, 1, 0, 0, 0],
                    "visibility": [10000.0, 10000.0, 10000.0, 10000.0, 10000.0, 10000.0],
                },
            },
        )

    transport = httpx.MockTransport(mock_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        # 1. Preset test: Córdoba Capital
        res = await get_route_weather(origin="rio_cuarto", destination="cordoba", client=client)
        assert res["origin"] == "rio_cuarto"
        assert res["destination"] == "cordoba"
        assert "RN 36" in res["route_name"]
        assert len(res["segments"]) == 3
        assert res["global_status"] == "optimo"
        assert res["max_rain_mm"] == 0.0
        assert res["max_gust_kmh"] == 28.0

        # Check segment midpoint
        midpoint = res["segments"][1]
        assert midpoint["segment_id"] == "midpoint"
        assert -33.0 < midpoint["lat"] < -31.5
        assert -65.0 < midpoint["lon"] < -64.0

        # 2. Custom lat/lon destination
        res_custom = await get_route_weather(
            origin="rio_cuarto",
            destination="custom",
            dest_lat=-32.5,
            dest_lon=-63.5,
            client=client,
        )
        assert res_custom["destination"] == "custom"
        assert len(res_custom["segments"]) == 3
        assert res_custom["segments"][2]["lat"] == -32.5
        assert res_custom["segments"][2]["lon"] == -63.5
