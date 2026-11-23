from datetime import datetime, timezone
import pytest
from backend.app.services.ensemble_engine import generate_ensemble_forecast
from backend.app.services.ml_corrector import (
    MLBiasCorrector,
    ml_corrector,
    haversine_distance_km,
    SAOC_LAT,
    SAOC_LON,
    TEMP_CLAMP_MIN,
    TEMP_CLAMP_MAX,
    WIND_CLAMP_MIN,
    WIND_CLAMP_MAX,
)

REF_TIME = datetime(2026, 11, 23, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_om_data():
    times = [f"2026-11-23T{(12 + h) % 24:02d}:00:00Z" for h in range(24)]
    hourly = []
    for t in times:
        hourly.append({
            "timestamp_utc": t,
            "models": {
                "ecmwf_ifs025": {
                    "temperature_c": 22.0,
                    "apparent_temperature_c": 22.0,
                    "relative_humidity_pct": 55.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 5.0,
                    "wind_speed_kmh": 15.0,
                    "wind_gust_kmh": 22.0,
                    "wind_direction_deg": 180.0,
                    "pressure_msl_hpa": 1012.0,
                    "surface_pressure_hpa": 962.0,
                    "weather_code": 1,
                    "cape_jkg": 150.0,
                },
                "icon_seamless": {
                    "temperature_c": 21.0,
                    "apparent_temperature_c": 21.0,
                    "relative_humidity_pct": 60.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 10.0,
                    "wind_speed_kmh": 16.0,
                    "wind_gust_kmh": 24.0,
                    "wind_direction_deg": 170.0,
                    "pressure_msl_hpa": 1011.5,
                    "surface_pressure_hpa": 961.5,
                    "weather_code": 1,
                    "cape_jkg": 120.0,
                },
                "gfs_seamless": {
                    "temperature_c": 23.0,
                    "apparent_temperature_c": 23.0,
                    "relative_humidity_pct": 50.0,
                    "precip_mm": 0.0,
                    "precip_prob_pct": 5.0,
                    "wind_speed_kmh": 14.0,
                    "wind_gust_kmh": 20.0,
                    "wind_direction_deg": 190.0,
                    "pressure_msl_hpa": 1012.5,
                    "surface_pressure_hpa": 963.0,
                    "weather_code": 1,
                    "cape_jkg": 180.0,
                },
            },
        })
    return {"hourly": hourly, "daily": []}


def test_ml_corrector_applies_in_rio_cuarto(sample_om_data):
    """Verifies ML bias correction is applied within 40 km of Rio Cuarto (SAOC)."""
    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_om_data,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        lat=-33.1232,
        lon=-64.3493,
        enable_ml=True,
    )

    h0 = res["hourly"][0]
    assert h0["ml_applied"] is True
    assert "raw_ensemble_temp_c" in h0
    assert "ml_temp_adjustment_c" in h0
    assert "ml_wind_adjustment_kmh" in h0

    # Adjusted temperature must equal raw ensemble + ML delta
    expected_temp = round(h0["raw_ensemble_temp_c"] + h0["ml_temp_adjustment_c"], 1)
    assert h0["temperature_c"] == expected_temp

    # ML metadata must be populated at root
    assert "ml_metadata" in res
    assert res["ml_metadata"]["available"] is True
    assert res["ml_metadata"]["station"] == "SAOC"
    assert res["ml_metadata"]["samples_matched"] > 1000


def test_ml_corrector_omitted_in_cordoba(sample_om_data):
    """Verifies ML corrector is strictly omitted in Cordoba (distance > 40 km from SAOC)."""
    cordoba_lat = -31.4201
    cordoba_lon = -64.1888
    dist = haversine_distance_km(cordoba_lat, cordoba_lon, SAOC_LAT, SAOC_LON)
    assert dist > 180.0  # Much larger than 40 km

    res = generate_ensemble_forecast(
        city_key="cordoba",
        open_meteo_data=sample_om_data,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=None,
        reference_time=REF_TIME,
        lat=cordoba_lat,
        lon=cordoba_lon,
        enable_ml=True,
    )

    # Every hourly record must NOT have ML applied
    for h in res["hourly"]:
        assert h["ml_applied"] is False
        assert h["ml_temp_adjustment_c"] == 0.0
        assert h["ml_wind_adjustment_kmh"] == 0.0
        assert h["temperature_c"] == h["raw_ensemble_temp_c"]
        assert h["wind_speed_kmh"] == h["raw_ensemble_wind_kmh"]


def test_valid_utc_hours_safeguard():
    """Verifies ML adjustment is rejected on hours excluded from valid_utc_hours."""
    corrector = ml_corrector
    original_valid = list(corrector.valid_utc_hours)

    try:
        # Restrict valid hours to only 12, 13, 14
        corrector.valid_utc_hours = [12, 13, 14]

        # Hour 12 UTC -> Valid
        t_adj_12, w_adj_12, applied_12 = corrector.compute_adjustments_for_step(
            timestamp_utc="2026-11-23T12:00:00Z",
            pred_temp_c=20.0,
            pred_rh_pct=50.0,
            pred_pressure_msl=1012.0,
            pred_precip_mm=0.0,
            pred_wind_kmh=15.0,
            pred_wind_dir_deg=180.0,
            city_key="rio_cuarto",
        )
        assert applied_12 is True

        # Hour 03 UTC -> Night gap, NOT in valid_utc_hours
        t_adj_03, w_adj_03, applied_03 = corrector.compute_adjustments_for_step(
            timestamp_utc="2026-11-23T03:00:00Z",
            pred_temp_c=20.0,
            pred_rh_pct=50.0,
            pred_pressure_msl=1012.0,
            pred_precip_mm=0.0,
            pred_wind_kmh=15.0,
            pred_wind_dir_deg=180.0,
            city_key="rio_cuarto",
        )
        assert applied_03 is False
        assert t_adj_03 == 0.0
        assert w_adj_03 == 0.0
    finally:
        corrector.valid_utc_hours = original_valid


def test_physical_clamping():
    """Verifies temperature and wind adjustments are clamped to safe physical limits."""
    assert TEMP_CLAMP_MIN == -4.5
    assert TEMP_CLAMP_MAX == 4.5
    assert WIND_CLAMP_MIN == -18.0
    assert WIND_CLAMP_MAX == 18.0

    corrector = ml_corrector
    t_adj, w_adj, applied = corrector.compute_adjustments_for_step(
        timestamp_utc="2026-11-23T12:00:00Z",
        pred_temp_c=45.0,  # Extreme temp
        pred_rh_pct=10.0,
        pred_pressure_msl=990.0,
        pred_precip_mm=100.0,
        pred_wind_kmh=120.0,  # Extreme wind
        pred_wind_dir_deg=90.0,
        city_key="rio_cuarto",
    )

    if applied:
        assert TEMP_CLAMP_MIN <= t_adj <= TEMP_CLAMP_MAX
        assert WIND_CLAMP_MIN <= w_adj <= WIND_CLAMP_MAX


def test_standardized_pressure_msl_sea_level(sample_om_data):
    """
    Verifies that pressure_hpa / surface_pressure_hpa standardizes to sea-level pressure (~1010 hPa)
    and NEVER shows station pressure (~958 hPa), even when WRF PSFC (~958 hPa) is present.
    """
    t0_str = sample_om_data["hourly"][0]["timestamp_utc"]

    # WRF report contains station pressure PSFC = 958.0 hPa
    wrf_data_with_psfc = {
        "wrf_available": True,
        "cities": {
            "rio_cuarto": {
                "hourly": [
                    {
                        "timestamp_utc": t0_str,
                        "temperature_c": 25.0,
                        "relative_humidity_pct": 50.0,
                        "wind_speed_kmh": 15.0,
                        "wind_direction_deg": 180.0,
                        "surface_pressure_hpa": 958.0,  # WRF PSFC at ~430m altitude
                    }
                ]
            }
        },
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_om_data,
        wrf_data=wrf_data_with_psfc,
        metar_data=None,
        reference_time=REF_TIME,
    )

    h0 = res["hourly"][0]
    # Sea-level pressure should be ~1012 hPa from Open-Meteo MSL, NEVER 958 hPa!
    assert h0["pressure_hpa"] >= 1000.0
    assert h0["surface_pressure_hpa"] >= 1000.0
    assert abs(h0["pressure_hpa"] - 1012.0) < 3.0
    assert h0["pressure_hpa"] != 958.0

    curr = res["current"]
    assert curr["pressure_hpa"] >= 1000.0
    assert curr["surface_pressure_hpa"] >= 1000.0
    assert curr["pressure_hpa"] != 958.0


def test_standardized_pressure_msl_with_fresh_metar_qnh(sample_om_data):
    """Verifies that fresh METAR QNH (~1013.4 hPa) dominates at t+0 and standardizes sea-level pressure."""
    fresh_metar = {
        "icao": "SAOC",
        "temp_c": 24.0,
        "wind_speed_kmh": 12.0,
        "qnh_hpa": 1013.4,
        "age_minutes": 20.0,
        "raw_metar": "METAR SAOC 231200Z 18006KT 24/14 Q1013",
    }

    res = generate_ensemble_forecast(
        city_key="rio_cuarto",
        open_meteo_data=sample_om_data,
        wrf_data={"wrf_available": False, "cities": {}},
        metar_data=fresh_metar,
        reference_time=REF_TIME,
    )

    h0 = res["hourly"][0]
    assert h0["pressure_hpa"] == 1013.4
    assert h0["surface_pressure_hpa"] == 1013.4
    assert res["current"]["pressure_hpa"] == 1013.4
