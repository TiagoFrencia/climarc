from datetime import datetime, timezone, timedelta
import pytest
from backend.app.services.ensemble_engine import generate_ensemble_forecast, generate_executive_summary, CORDOBA_TZ

REF_TIME = datetime(2026, 11, 23, 0, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_open_meteo():
    times = [(REF_TIME + timedelta(hours=h)).strftime("%Y-%m-%dT%H:00:00Z") for h in range(72)]
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


def test_parse_metar_heavy_ts_vs_hail():
    """WMO 99 is reserved exclusively for GR/GS (Hail), while +TSRA/+TS maps to WMO 96 (Heavy TS)."""
    from backend.app.services.ensemble_engine import parse_metar_phenomenon

    # 1. Hail (+TSRA GR) -> WMO 99
    metar_hail = {
        "raw_metar": "METAR SAOC 231300Z 18025KT 4000 +TSRA GR BKN015CB 16/15 Q1008",
        "wx_string": "+TSRA GR",
        "wx_codes": ["+TSRA", "GR"],
    }
    w_code, wx_raw, is_p = parse_metar_phenomenon(metar_hail)
    assert w_code == 99
    assert is_p is True

    # 2. Heavy Thunderstorm (+TSRA without hail) -> WMO 96
    metar_heavy_ts = {
        "raw_metar": "METAR SAOC 231300Z 18025KT 4000 +TSRA BKN015CB 16/15 Q1008",
        "wx_string": "+TSRA",
        "wx_codes": ["+TSRA"],
    }
    w_code, wx_raw, is_p = parse_metar_phenomenon(metar_heavy_ts)
    assert w_code == 96
    assert is_p is True

    # 3. Standard Thunderstorm (TSRA / TS) -> WMO 95
    metar_ts = {
        "raw_metar": "METAR SAOC 230900Z 02008KT 9999 TS SCT030 FEW045CB 15/14 Q1009",
        "wx_string": "TS",
        "wx_codes": ["TS"],
    }
    w_code, wx_raw, is_p = parse_metar_phenomenon(metar_ts)
    assert w_code == 95


def test_precip_probability_cap_when_zero_mm(sample_open_meteo):
    """When all active models predict < 0.1 mm and WRF nearby < 0.5 mm, precip_prob_pct is capped at 35%."""
    # Step 19: All models predict 0.0 mm but high probabilities (87%, 69%, 65%)
    h19 = sample_open_meteo["hourly"][19]
    h19["models"]["ecmwf_ifs025"]["precip_mm"] = 0.0
    h19["models"]["ecmwf_ifs025"]["precip_prob_pct"] = 87.0
    h19["models"]["icon_seamless"]["precip_mm"] = 0.0
    h19["models"]["icon_seamless"]["precip_prob_pct"] = 69.0
    h19["models"]["gfs_seamless"]["precip_mm"] = 0.0
    h19["models"]["gfs_seamless"]["precip_prob_pct"] = 65.0

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    step19_out = res["hourly"][19]
    assert step19_out["precip_mm"] == 0.0
    assert step19_out["precip_prob_pct"] <= 35.0


def test_daily_fused_hourly_integration(sample_open_meteo):
    """daily[0] and daily[1] min/max must integrate the real fused hourly values instead of raw GFS."""
    # Initialize all 72 hours to a baseline of 17.0 C
    for h in sample_open_meteo["hourly"]:
        for m in ("ecmwf_ifs025", "icon_seamless", "gfs_seamless"):
            h["models"][m]["temperature_c"] = 17.0

    # Today in Cordoba (UTC-3): indices 3 to 26
    # Low of 14.0 at index 5 and peak of 19.5 at index 15
    for m in ("ecmwf_ifs025", "icon_seamless", "gfs_seamless"):
        sample_open_meteo["hourly"][5]["models"][m]["temperature_c"] = 14.0
        sample_open_meteo["hourly"][15]["models"][m]["temperature_c"] = 19.5

    # But raw daily block had GFS saying 26.0 C
    sample_open_meteo["daily"][0]["models"]["gfs_seamless"]["temperature_max_c"] = 26.0

    # Tomorrow in Cordoba (UTC-3): indices 27 to 50
    # Add day 1 (tomorrow: 2026-11-24) in sample_open_meteo
    sample_open_meteo["daily"].append({
        "date": "2026-11-24",
        "models": {
            "ecmwf_ifs025": {"temperature_max_c": 30.0, "temperature_min_c": 12.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 0.0, "wind_speed_max_kmh": 15.0, "weather_code": 1},
            "icon_seamless": {"temperature_max_c": 28.0, "temperature_min_c": 11.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 0.0, "wind_speed_max_kmh": 15.0, "weather_code": 1},
            "gfs_seamless": {"temperature_max_c": 32.0, "temperature_min_c": 14.0, "precipitation_sum_mm": 0.0, "precipitation_probability_max_pct": 0.0, "wind_speed_max_kmh": 15.0, "weather_code": 1},
        },
    })
    # Set tomorrow's low of 13.0 at index 28 and peak of 21.0 at index 38
    for m in ("ecmwf_ifs025", "icon_seamless", "gfs_seamless"):
        sample_open_meteo["hourly"][28]["models"][m]["temperature_c"] = 13.0
        sample_open_meteo["hourly"][38]["models"][m]["temperature_c"] = 21.0

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    d0 = res["daily"][0]
    d1 = res["daily"][1]

    # Daily[0] max must be 19.5 (from fused hourly) not 26.0 (from GFS)
    assert d0["temperature_max_c"] == 19.5
    # Daily[1] max must be 21.0 and min 13.0 (from tomorrow's fused hourly)
    assert d1["temperature_max_c"] == 21.0
    assert d1["temperature_min_c"] == 13.0


def test_strict_live_rain_truth_no_ghost_rain(sample_open_meteo):
    """
    CRITERIO 1: Modelo con 1.0 mm pero sin METAR de lluvia -> is_precipitating_now DEBE ser False.
    Un modelo es pronóstico, no observación en vivo.
    """
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["precip_mm"] = 1.0
    h0["models"]["icon_seamless"]["precip_mm"] = 1.0
    h0["models"]["gfs_seamless"]["precip_mm"] = 1.0
    h0["models"]["ecmwf_ifs025"]["weather_code"] = 61

    # Case A: Without any METAR
    res_no_metar = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_no_metar["current"]["is_precipitating_now"] is False

    # Case B: With fresh METAR that does NOT report runway precipitation
    metar_fair = {
        "icao": "SAOC",
        "age_minutes": 15.0,
        "temp_c": 22.0,
        "dewp_c": 12.0,
        "humidity_pct": 53.0,
        "wind_speed_kmh": 14.0,
        "qnh_hpa": 1014.0,
        "raw_metar": "METAR SAOC 230000Z 08008KT CAVOK 22/12 Q1014",
        "sky_condition": "clear",
    }
    res_metar_fair = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_fair,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_metar_fair["current"]["is_precipitating_now"] is False


def test_bidirectional_metar_sky_override(sample_open_meteo):
    """
    CRITERIO 2: METAR fresco con CAVOK/FEW sin lluvia + modelo con weather_code=61 -> sobrescribe a 0/1.
    """
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["weather_code"] = 61
    h0["models"]["icon_seamless"]["weather_code"] = 61
    h0["models"]["gfs_seamless"]["weather_code"] = 61

    # Subcase A: CAVOK -> 0 (Despejado)
    metar_cavok = {
        "icao": "SAOC",
        "age_minutes": 25.0,
        "temp_c": 24.0,
        "dewp_c": 11.0,
        "humidity_pct": 45.0,
        "wind_speed_kmh": 12.0,
        "qnh_hpa": 1015.0,
        "raw_metar": "METAR SAOC 230000Z 07006KT CAVOK 24/11 Q1015",
        "sky_condition": "clear",
    }
    res_cavok = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_cavok,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_cavok["current"]["weather_code"] == 0
    assert res_cavok["hourly"][0]["weather_code"] == 0
    assert res_cavok["current"]["is_precipitating_now"] is False

    # Subcase B: FEW -> 1 (Mayormente despejado)
    metar_few = {
        "icao": "SAOC",
        "age_minutes": 20.0,
        "temp_c": 23.0,
        "dewp_c": 13.0,
        "humidity_pct": 53.0,
        "wind_speed_kmh": 10.0,
        "qnh_hpa": 1014.0,
        "raw_metar": "METAR SAOC 230000Z 09005KT 9999 FEW030 23/13 Q1014",
        "sky_condition": "few",
    }
    res_few = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_few,
        reference_time=REF_TIME,
        enable_ml=False,
    )
    assert res_few["current"]["weather_code"] == 1
    assert res_few["hourly"][0]["weather_code"] == 1
    assert res_few["current"]["is_precipitating_now"] is False


def test_stale_metar_rain_does_not_override(sample_open_meteo):
    """
    CRITERIO 3: METAR viejo (66 min) con -RA -> NO fuerza lluvia actual ni is_precipitating_now.
    """
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["weather_code"] = 1
    h0["models"]["icon_seamless"]["weather_code"] = 1
    h0["models"]["gfs_seamless"]["weather_code"] = 1

    metar_stale_rain = {
        "icao": "SAOC",
        "age_minutes": 66.0,
        "temp_c": 18.0,
        "dewp_c": 16.0,
        "humidity_pct": 88.0,
        "wind_speed_kmh": 16.0,
        "qnh_hpa": 1012.0,
        "wx_string": "-RA",
        "raw_metar": "METAR SAOC 230000Z 04008KT 5000 -RA BKN020 18/16 Q1012",
        "sky_condition": "broken",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=metar_stale_rain,
        reference_time=REF_TIME,
        enable_ml=False,
    )

    # Stale rain METAR must NOT trigger live precipitation or force rain code
    assert res["current"]["is_precipitating_now"] is False
    assert res["current"]["weather_code"] != 51
    assert res["hourly"][0]["weather_code"] != 51


def test_goes19_clear_sky_override(sample_open_meteo):
    """
    CRITERIO 4: GOES-19 con center_brightness_temp_c = 16°C y surface_temp = 18°C + modelo con lluvia -> corrige a cielo despejado (1).
    """
    h0 = sample_open_meteo["hourly"][0]
    h0["models"]["ecmwf_ifs025"]["temperature_c"] = 18.0
    h0["models"]["icon_seamless"]["temperature_c"] = 18.0
    h0["models"]["gfs_seamless"]["temperature_c"] = 18.0
    h0["models"]["ecmwf_ifs025"]["weather_code"] = 61  # Model forcing rain

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_open_meteo,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        enable_ml=False,
        center_brightness_temp_c=16.0,  # 18°C surface - 16°C satellite TB = 2°C <= 8.0°C (warm ground)
    )

    assert res["current"]["is_precipitating_now"] is False
    assert res["current"]["weather_code"] == 1
    assert res["hourly"][0]["weather_code"] == 1


def test_executive_summary_never_claims_active_rain_when_not_precipitating():
    """
    CRITERIO 5: Asegurar que generate_executive_summary nunca diga 'Lluvia activa en la zona' si is_precipitating_now es False.
    """
    current = {
        "weather_code": 61,
        "is_precipitating_now": False,
        "temperature_c": 19.0,
        "wind_speed_kmh": 12.0,
        "wind_direction_deg": 180.0,
    }
    hourly = [
        {"timestamp_utc": "2026-11-23T22:00:00Z", "weather_code": 61, "precip_mm": 1.0, "precip_prob_pct": 70},
        {"timestamp_utc": "2026-11-23T23:00:00Z", "weather_code": 61, "precip_mm": 0.5, "precip_prob_pct": 50},
    ]
    daily = [{"temperature_max_c": 22.0, "temperature_min_c": 14.0}]

    summary = generate_executive_summary(current=current, hourly=hourly, daily=daily)
    assert "Lluvia activa en la zona" not in summary


def test_executive_summary_filters_past_hours_for_upcoming_rain():
    """
    Verifica que generate_executive_summary nunca anuncie 'desde las 19:00' si las 19:00 ya pasaron.
    Solo debe anunciar horas estrictamente futuras (> now_local).
    """
    now_utc = datetime.now(timezone.utc)
    # Step in the past (e.g. 20 min ago)
    past_iso = (now_utc - timedelta(minutes=20)).replace(minute=0, second=0).strftime("%Y-%m-%dT%H:00:00Z")
    # Step in the future (e.g. 2 hours ahead)
    future_iso = (now_utc + timedelta(hours=2)).strftime("%Y-%m-%dT%H:00:00Z")
    future_local_hour = (now_utc + timedelta(hours=2)).astimezone(CORDOBA_TZ).strftime("%H:00")

    current = {
        "weather_code": 1,
        "is_precipitating_now": False,
        "temperature_c": 21.0,
        "wind_speed_kmh": 10.0,
        "wind_direction_deg": 180.0,
    }
    hourly = [
        {"timestamp_utc": past_iso, "weather_code": 61, "precip_mm": 2.0, "precip_prob_pct": 80},
        {"timestamp_utc": future_iso, "weather_code": 61, "precip_mm": 1.5, "precip_prob_pct": 75},
    ]
    daily = [{"temperature_max_c": 25.0, "temperature_min_c": 15.0}]

    summary = generate_executive_summary(current=current, hourly=hourly, daily=daily)
    past_local_hour = (now_utc - timedelta(minutes=20)).replace(minute=0, second=0).astimezone(CORDOBA_TZ).strftime("%H:00")
    assert f"desde las {past_local_hour}" not in summary
    assert f"desde las {future_local_hour}" in summary



