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
        enable_ml=False,
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
        enable_ml=False,
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


def test_metar_weather_code_override(sample_open_meteo):
    # Models say weather_code=1 (clear/mainly clear)
    # METAR reports TSRA with age < 90 min
    metar_thunderstorm = {
        "icao": "SAOC",
        "age_minutes": 15.0,
        "temp_c": 18.0,
        "dewp_c": 16.0,
        "humidity_pct": 88.0,
        "wind_speed_kmh": 25.0,
        "qnh_hpa": 1011.0,
        "wx_string": "TSRA",
        "raw_metar": "METAR SAOC 230000Z 02014KT 4000 TSRA BKN020 18/16 Q1011",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_thunderstorm,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    curr = res["current"]
    assert curr["weather_code"] == 95  # Thunderstorm
    assert res["hourly"][0]["weather_code"] == 95
    assert curr["is_precipitating_now"] is True
    assert curr["metar_wx_raw"] == "TSRA"

    # Test hail / severe thunderstorm override: +TSRA GR -> 99
    metar_hail = {
        "icao": "SAOC",
        "age_minutes": 10.0,
        "temp_c": 17.0,
        "dewp_c": 15.0,
        "humidity_pct": 90.0,
        "wind_speed_kmh": 35.0,
        "qnh_hpa": 1009.0,
        "wx_string": "+TSRA GR",
        "raw_metar": "METAR SAOC 230000Z 02020KT 2000 +TSRA GR BKN015 17/15 Q1009",
    }
    res_hail = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_hail,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_hail["current"]["weather_code"] == 99
    assert res_hail["current"]["is_precipitating_now"] is True

    # Test rain override: RA -> 63
    metar_rain = {
        "icao": "SAOC",
        "age_minutes": 20.0,
        "temp_c": 16.0,
        "dewp_c": 15.0,
        "humidity_pct": 94.0,
        "wind_speed_kmh": 12.0,
        "qnh_hpa": 1010.0,
        "wx_string": "RA",
        "raw_metar": "METAR SAOC 230000Z 02006KT 5000 RA OVC020 16/15 Q1010",
    }
    res_rain = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_rain,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_rain["current"]["weather_code"] == 63
    assert res_rain["current"]["is_precipitating_now"] is True


def test_apparent_temperature_and_dew_point(sample_open_meteo):
    # Anchor at 15.0 C, 85% RH, 14.0 km/h wind
    metar = {
        "icao": "SAOC",
        "age_minutes": 10.0,
        "temp_c": 15.0,
        "dewp_c": 12.5,
        "humidity_pct": 85.0,
        "wind_speed_kmh": 14.0,
        "qnh_hpa": 1010.0,
        "wx_string": None,
        "raw_metar": "METAR SAOC 230000Z 02008KT 9999 BKN030 15/13 Q1010",
    }

    # Set sample open meteo to same values
    h0 = sample_open_meteo["hourly"][0]
    for m in ("ecmwf_ifs025", "icon_seamless", "gfs_seamless"):
        h0["models"][m]["temperature_c"] = 15.0
        h0["models"][m]["relative_humidity_pct"] = 85.0
        h0["models"][m]["wind_speed_kmh"] = 14.0

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    curr = res["current"]
    assert curr["temperature_c"] == 15.0
    # Steadman apparent temperature with 14 km/h wind must be colder than 15 C (around 13.1 C)
    assert curr["apparent_temperature_c"] < 15.0
    assert 12.5 <= curr["apparent_temperature_c"] <= 13.5
    # Dew point must be exposed and equal to METAR dewp_c
    assert curr["dew_point_c"] == 12.5


def test_daily_forecast_coherence(sample_open_meteo):
    # Daily[0] predicted min 18.0 C, but current temperature is 15.0 C
    sample_open_meteo["daily"][0]["models"]["ecmwf_ifs025"]["temperature_min_c"] = 18.0
    sample_open_meteo["daily"][0]["models"]["icon_seamless"]["temperature_min_c"] = 18.0
    sample_open_meteo["daily"][0]["models"]["gfs_seamless"]["temperature_min_c"] = 18.0

    # Also set daily[0] with weather_code 61 (rain) but 0% prob and 0.0 mm
    sample_open_meteo["daily"][0]["models"]["ecmwf_ifs025"]["weather_code"] = 61
    sample_open_meteo["daily"][0]["models"]["ecmwf_ifs025"]["precipitation_probability_max_pct"] = 5.0
    sample_open_meteo["daily"][0]["models"]["ecmwf_ifs025"]["precipitation_sum_mm"] = 0.0
    sample_open_meteo["daily"][0]["models"]["icon_seamless"]["precipitation_probability_max_pct"] = 5.0
    sample_open_meteo["daily"][0]["models"]["icon_seamless"]["precipitation_sum_mm"] = 0.0
    sample_open_meteo["daily"][0]["models"]["gfs_seamless"]["precipitation_probability_max_pct"] = 5.0
    sample_open_meteo["daily"][0]["models"]["gfs_seamless"]["precipitation_sum_mm"] = 0.0

    metar = {
        "icao": "SAOC",
        "age_minutes": 10.0,
        "temp_c": 15.0,
        "dewp_c": 10.0,
        "humidity_pct": 70.0,
        "wind_speed_kmh": 10.0,
        "qnh_hpa": 1010.0,
        "wx_string": None,
        "raw_metar": "METAR SAOC 230000Z 02005KT 9999 SKC 15/10 Q1010",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    d0 = res["daily"][0]
    # FIX 4.1: Daily min must not exceed current temperature (15.0)
    assert d0["temperature_min_c"] <= 15.0

    # FIX 4.2: Daily weather code must be normalized to 3 (cloudy) instead of rain icon with 0%
    assert d0["weather_code"] == 3
