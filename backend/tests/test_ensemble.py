from datetime import datetime, timezone
import pytest
from backend.app.services.ensemble_engine import generate_ensemble_forecast

REF_TIME = datetime(2026, 11, 23, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_open_meteo():
    times = [f"2026-11-23T{h:02d}:00:00Z" for h in range(72)]
    hourly = []
    for t in times:
        hourly.append({
            "timestamp_utc": t,
            "models": {
                "ecmwf_ifs025": {
                    "temperature_c": 20.0,
                    "apparent_temperature_c": 20.0,
                    "relative_humidity_pct": 50.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 10.0,
                    "wind_speed_kmh": 10.0,
                    "wind_gust_kmh": 15.0,
                    "wind_direction_deg": 180.0,
                    "surface_pressure_hpa": 1010.0,
                    "weather_code": 1,
                    "cape_jkg": 100.0,
                },
                "icon_seamless": {
                    "temperature_c": 20.0,
                    "apparent_temperature_c": 20.0,
                    "relative_humidity_pct": 50.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 10.0,
                    "wind_speed_kmh": 10.0,
                    "wind_gust_kmh": 15.0,
                    "wind_direction_deg": 180.0,
                    "surface_pressure_hpa": 1010.0,
                    "weather_code": 1,
                    "cape_jkg": 100.0,
                },
                "gfs_seamless": {
                    "temperature_c": 20.0,
                    "apparent_temperature_c": 20.0,
                    "relative_humidity_pct": 50.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 10.0,
                    "wind_speed_kmh": 10.0,
                    "wind_gust_kmh": 15.0,
                    "wind_direction_deg": 180.0,
                    "surface_pressure_hpa": 1010.0,
                    "weather_code": 1,
                    "cape_jkg": 100.0,
                },
            },
        })
    daily = [
        {
            "date": "2026-11-23",
            "models": {
                "ecmwf_ifs025": {"temperature_max_c": 25.0, "temperature_min_c": 15.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 10.0, "wind_speed_max_kmh": 20.0, "weather_code": 1},
                "icon_seamless": {"temperature_max_c": 24.0, "temperature_min_c": 14.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 10.0, "wind_speed_max_kmh": 18.0, "weather_code": 1},
                "gfs_seamless": {"temperature_max_c": 26.0, "temperature_min_c": 16.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 10.0, "wind_speed_max_kmh": 22.0, "weather_code": 1},
            },
        }
    ]
    return {"hourly": hourly, "daily": daily}


def test_ensemble_without_wrf(sample_open_meteo):
    # Set differing temperatures: ECMWF=20, ICON=16, GFS=10
    # Weights without WRF: ECMWF 55%, ICON 25%, GFS 20%
    # Expected temp: 0.55*20 + 0.25*16 + 0.20*10 = 11.0 + 4.0 + 2.0 = 17.0
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["temperature_c"] = 20.0
    h0["models"]["icon_seamless"]["temperature_c"] = 16.0
    h0["models"]["gfs_seamless"]["temperature_c"] = 10.0

    wrf_empty = {"wrf_available": False, "cities": {}}

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data=wrf_empty,
        metar_data=None,
        reference_time=REF_TIME,
    )

    assert res["wrf_available"] is False
    assert "wrf_smn" not in res["models_active"]
    h_res = res["hourly"][0]
    assert h_res["temperature_c"] == 17.0


def test_ensemble_with_wrf(sample_open_meteo):
    # Set differing temperatures: WRF=30, ECMWF=20, ICON=10, GFS=10
    # Weights with fresh WRF: WRF 35%, ECMWF 35%, ICON 15%, GFS 15%
    # Expected temp: 0.35*30 + 0.35*20 + 0.15*10 + 0.15*10 = 10.5 + 7.0 + 1.5 + 1.5 = 20.5
    h0 = sample_open_meteo["hourly"][0]
    t0_str = h0["timestamp_utc"]
    h0["models"]["ecmwf_ifs025"]["temperature_c"] = 20.0
    h0["models"]["icon_seamless"]["temperature_c"] = 10.0
    h0["models"]["gfs_seamless"]["temperature_c"] = 10.0

    wrf_data = {
        "wrf_available": True,
        "cities": {
            "rio_cuarto": {
                "hourly": [
                    {
                        "timestamp_utc": t0_str,
                        "temperature_c": 30.0,
                        "relative_humidity_pct": 60.0,
                        "wind_speed_kmh": 20.0,
                        "wind_direction_deg": 180.0,
                        "wind_nearby_max_kmh": 25.0,
                        "precip_mm": 2.0,
                        "precip_nearby_max_mm": 4.5,
                        "surface_pressure_hpa": 1005.0,
                    }
                ]
            }
        },
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data=wrf_data,
        metar_data=None,
        reference_time=REF_TIME,
    )

    assert res["wrf_available"] is True
    assert "wrf_smn" in res["models_active"]
    h_res = res["hourly"][0]
    assert h_res["temperature_c"] == 20.5
    assert h_res["wrf_nearby_precip_max_mm"] == 4.5
    assert "wrf" in h_res["models_comparison"]
    assert h_res["models_comparison"]["wrf"]["temperature_c"] == 30.0


def test_confidence_index(sample_open_meteo):
    # Models agree tightly: confidence should be 'alta'
    res_tight = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
    )
    assert res_tight["hourly"][0]["confidence"] == "alta"

    # Models diverge heavily: temp 10, 25, 40 -> confidence should be 'baja'
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["temperature_c"] = 10.0
    h0["models"]["icon_seamless"]["temperature_c"] = 25.0
    h0["models"]["gfs_seamless"]["temperature_c"] = 40.0
    res_divergent = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
    )
    assert res_divergent["hourly"][0]["confidence"] == "baja"
