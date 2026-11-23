from datetime import datetime, timezone
import pytest
from backend.app.services.ensemble_engine import generate_ensemble_forecast

REF_TIME = datetime(2026, 11, 23, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def base_forecast():
    times = [f"2026-11-23T{h:02d}:00:00Z" for h in range(72)]
    hourly = []
    for t in times:
        hourly.append({
            "timestamp_utc": t,
            "models": {
                "ecmwf_ifs025": {"temperature_c": 20.0, "wind_speed_kmh": 20.0, "precip_mm": 0.0},
                "icon_seamless": {"temperature_c": 20.0, "wind_speed_kmh": 20.0, "precip_mm": 0.0},
                "gfs_seamless": {"temperature_c": 20.0, "wind_speed_kmh": 20.0, "precip_mm": 0.0},
            },
        })
    return {"hourly": hourly, "daily": []}


def test_nowcast_blending_fresh_metar(base_forecast):
    # METAR observation is 15 minutes old:
    # Metar Temp: 30.0 C, Wind: 10.0 km/h
    # Ensemble raw: 20.0 C, Wind: 20.0 km/h
    # t+0 expected: 0.70*30 + 0.30*20 = 21 + 6 = 27.0 C
    #               0.70*10 + 0.30*20 = 7 + 6 = 13.0 km/h
    # t+1 expected: 0.35*30 + 0.65*20 = 10.5 + 13.0 = 23.5 C
    #               0.35*10 + 0.65*20 = 3.5 + 13.0 = 16.5 km/h
    # t+2 expected: pure ensemble = 20.0 C, 20.0 km/h

    fresh_metar = {
        "icao": "SAOC",
        "temp_c": 30.0,
        "wind_speed_kmh": 10.0,
        "humidity_pct": 70.0,
        "age_minutes": 15.0,
        "raw_metar": "METAR SAOC 230000Z 18005KT 30/15 Q1010",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=base_forecast,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=fresh_metar,
        reference_time=REF_TIME,
    )

    h0 = res["hourly"][0]
    h1 = res["hourly"][1]
    h2 = res["hourly"][2]

    assert h0["nowcast_blended"] is True
    assert h0["temperature_c"] == 27.0
    assert h0["wind_speed_kmh"] == 13.0

    assert h1["nowcast_blended"] is True
    assert h1["temperature_c"] == 23.5
    assert h1["wind_speed_kmh"] == 16.5

    assert h2["nowcast_blended"] is False
    assert h2["temperature_c"] == 20.0
    assert h2["wind_speed_kmh"] == 20.0


def test_nowcast_blending_stale_metar(base_forecast):
    # METAR observation is 120 minutes old (> 90 min threshold)
    # No blending should be applied!
    stale_metar = {
        "icao": "SAOC",
        "temp_c": 30.0,
        "wind_speed_kmh": 10.0,
        "age_minutes": 120.0,
        "raw_metar": "METAR SAOC 230000Z 18005KT 30/15 Q1010",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=base_forecast,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=stale_metar,
        reference_time=REF_TIME,
    )

    h0 = res["hourly"][0]
    assert h0["nowcast_blended"] is False
    assert h0["temperature_c"] == 20.0
    assert h0["wind_speed_kmh"] == 20.0
