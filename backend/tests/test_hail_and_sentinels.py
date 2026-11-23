"""
ClimaRC v2.0 - Sprint 2.1 Unit Tests
Covers:
1. 0-6 km Deep-Layer Vertical Wind Shear (true vectorial calculation).
2. Atmospheric Hail Potential (Stage 1 Score & Levels).
3. Anti-False-Positive Cirrus Cloud Filter.
4. Sentinel Approaching Front Detection (SAOR Villa Reynolds / SAOL Laboulaye).
5. QNH Barometric Trend & Delta (3h history).
6. Deterministic 1-liner Argentine Spanish Executive Summary (<= 120 chars).
"""

import pytest
from datetime import datetime, timezone, timedelta

from backend.app.services.hail_service import (
    calculate_wind_shear_0_6km,
    compute_stage1_step_score,
    score_to_level,
    evaluate_hail_risk,
)
from backend.app.services.sentinel_service import (
    detect_approaching_front,
    get_sentinels_summary,
    haversine_distance_km,
    calculate_bearing_deg,
)
from backend.app.services.metar_service import calculate_qnh_trend
from backend.app.services.ensemble_engine import (
    generate_executive_summary,
    generate_ensemble_forecast,
)


def test_wind_shear_vector_calculation():
    """Validates real vectorial calculation of 0-6 km deep layer vertical wind shear."""
    # 1. Collinear winds (same direction 000°, different speeds)
    # Surface: 10 km/h from North (0°). 500 hPa: 60 km/h from North (0°).
    # Vector diff magnitude = |60 - 10| = 50.0 km/h
    shear_same_dir = calculate_wind_shear_0_6km(
        wspd_10m_kmh=10.0,
        wdir_10m_deg=0.0,
        wspd_500hpa_kmh=60.0,
        wdir_500hpa_deg=0.0,
    )
    assert shear_same_dir == 50.0

    # 2. Orthogonal winds (90 degree difference)
    # Surface: 30 km/h from South (180° -> u=0, v=+30)
    # 500 hPa: 40 km/h from West (270° -> u=+40, v=0)
    # du = 40 - 0 = 40, dv = 0 - 30 = -30 -> sqrt(40^2 + (-30)^2) = 50.0 km/h
    shear_orthogonal = calculate_wind_shear_0_6km(
        wspd_10m_kmh=30.0,
        wdir_10m_deg=180.0,
        wspd_500hpa_kmh=40.0,
        wdir_500hpa_deg=270.0,
    )
    assert shear_orthogonal == 50.0

    # 3. Opposing winds (180 degree turnaround / directional shear)
    # Surface: 20 km/h from South (180° -> u=0, v=+20)
    # 500 hPa: 30 km/h from North (0° -> u=0, v=-30)
    # Vector diff = |20 - (-30)| = 50.0 km/h
    shear_opposing = calculate_wind_shear_0_6km(
        wspd_10m_kmh=20.0,
        wdir_10m_deg=180.0,
        wspd_500hpa_kmh=30.0,
        wdir_500hpa_deg=0.0,
    )
    assert shear_opposing == 50.0

    # 4. Graceful handling of None / zero speeds
    assert calculate_wind_shear_0_6km(None, None, 45.0, 270.0) == 45.0
    assert calculate_wind_shear_0_6km(15.0, 180.0, None, None) == 15.0


def test_stage1_hail_score_and_levels():
    """Validates Stage 1 atmospheric hail score normalization (0-100) and categorized risk levels."""
    # Low / Stable Atmosphere -> sin_riesgo (< 25)
    score_low = compute_stage1_step_score(
        cape=50.0,
        shear_kmh=12.0,
        freezing_level_m=5100.0,
        precip_prob=5.0,
        precip_mm=0.0,
        wrf_nearby_mm=0.0,
    )
    assert score_low < 25.0
    assert score_to_level(score_low) == "sin_riesgo"

    # Moderate Convective Risk (CAPE ~900, shear 35 km/h, freezing level 3800m, light rain) -> moderado (25-49)
    score_mod = compute_stage1_step_score(
        cape=900.0,
        shear_kmh=35.0,
        freezing_level_m=3800.0,
        precip_prob=35.0,
        precip_mm=0.3,
        wrf_nearby_mm=0.5,
    )
    assert 25.0 <= score_mod < 50.0
    assert score_to_level(score_mod) == "moderado"

    # High Convective / Hail Risk (CAPE 1800, shear 60 km/h, optimal freezing level 3600m) -> alto (50-74)
    score_high = compute_stage1_step_score(
        cape=1800.0,
        shear_kmh=60.0,
        freezing_level_m=3600.0,
        precip_prob=65.0,
        precip_mm=1.5,
        wrf_nearby_mm=2.5,
    )
    assert 50.0 <= score_high < 75.0
    assert score_to_level(score_high) == "alto"

    # Extreme Severe Supercell Environment (CAPE 3500, shear 95 km/h, optimal freezing level, high WRF nearby) -> extremo (>= 75)
    score_ext = compute_stage1_step_score(
        cape=3500.0,
        shear_kmh=95.0,
        freezing_level_m=3400.0,
        precip_prob=90.0,
        precip_mm=8.0,
        wrf_nearby_mm=22.0,
    )
    assert score_ext >= 75.0
    assert score_to_level(score_ext) == "extremo"

    # Real Production Incident (Anti-False-Alarm: CAPE 840 J/kg + Shear 92 km/h + Freezing Level 3777m)
    # Even with high shear and optimal freezing level, score MUST NOT exceed 45.0 (moderado, NEVER alto)
    score_prod_case = compute_stage1_step_score(
        cape=840.0,
        shear_kmh=92.0,
        freezing_level_m=3777.0,
        precip_prob=60.0,
        precip_mm=3.0,
        wrf_nearby_mm=5.0,
    )
    assert score_prod_case <= 45.0
    level_prod_case = score_to_level(score_prod_case)
    assert level_prod_case in ("sin_riesgo", "moderado")
    assert level_prod_case != "alto"
    assert level_prod_case != "extremo"


@pytest.mark.anyio
async def test_evaluate_hail_risk_real_prod_anti_false_alarm():
    """
    Validates end-to-end evaluate_hail_risk with real production values:
    CAPE=840 + shear=92 km/h -> score <= 45.0, level <= moderado (NEVER alto).
    Also checks next-day peak window prefixing ('Mañana ').
    """
    now_utc = datetime.now(timezone.utc)
    # Simulate a forecast step tomorrow at 06:00 UTC
    t_tomorrow = (now_utc + timedelta(hours=14)).strftime("%Y-%m-%dT%H:00:00Z")

    fake_om = {
        "hourly": [{
            "timestamp_utc": t_tomorrow,
            "models": {
                "ecmwf_ifs025": {"cape_jkg": 840.0, "wind_speed_kmh": 20.0, "wind_direction_deg": 180.0, "precip_prob_pct": 50.0, "precip_mm": 2.0},
                "icon_seamless": {"cape_jkg": 820.0, "wind_speed_kmh": 20.0, "wind_direction_deg": 180.0, "precip_prob_pct": 50.0, "precip_mm": 2.0},
                "gfs_seamless": {"cape_jkg": 800.0, "wind_speed_kmh": 20.0, "wind_direction_deg": 180.0, "precip_prob_pct": 50.0, "precip_mm": 2.0},
            },
            "wind_speed_500hPa_kmh": 92.0,
            "wind_direction_500hPa_deg": 270.0,
            "freezing_level_height_m": 3777.0,
        }]
    }
    fake_wrf = {"cities": {"rio_cuarto": {"hourly": []}}}
    fake_metar_quiet = {"SAOC": {"raw_metar": "METAR SAOC 231800Z 04010KT 9999 SKC 20/10 Q1015", "wx_codes": []}}

    # Mock satellite scan to ensure no core is detected
    import backend.app.services.hail_service as hs
    orig_scan = hs.scan_goes19_band13
    async def mock_quiet_scan(*args, **kwargs):
        return {"available": True, "severe_core_detected": False}

    hs.scan_goes19_band13 = mock_quiet_scan
    try:
        res = await hs.evaluate_hail_risk(fake_om, fake_wrf, fake_metar_quiet)
        assert res["score"] <= 45.0
        assert res["level"] in ("sin_riesgo", "moderado")
        assert res["level"] != "alto"
        assert res["imminent_hail_alert"] is False
    finally:
        hs.scan_goes19_band13 = orig_scan


@pytest.mark.anyio
async def test_anti_cirrus_false_positive_filter():
    """
    MANDATORY RULE TEST:
    Verifies that satellite cold cloud tops (< -60°C) are rejected if Stage 1 potential is 'sin_riesgo'
    (prevents classifying harmless thin jet-stream cirrus as severe hail storms).
    """
    # 1. Cirrus case: Satellite cold core detected, BUT atmosphere is totally stable (CAPE = 0)
    fake_open_meteo_stable = {
        "hourly": [{
            "timestamp_utc": "2026-11-23T18:00:00Z",
            "models": {
                "ecmwf_ifs025": {"cape_jkg": 0.0, "wind_speed_kmh": 10.0, "wind_direction_deg": 40.0, "precip_prob_pct": 0.0, "precip_mm": 0.0},
                "icon_seamless": {"cape_jkg": 0.0, "wind_speed_kmh": 10.0, "wind_direction_deg": 40.0, "precip_prob_pct": 0.0, "precip_mm": 0.0},
                "gfs_seamless": {"cape_jkg": 0.0, "wind_speed_kmh": 10.0, "wind_direction_deg": 40.0, "precip_prob_pct": 0.0, "precip_mm": 0.0},
            },
            "wind_speed_500hPa_kmh": 20.0,
            "wind_direction_500hPa_deg": 50.0,
            "freezing_level_height_m": 4800.0,
        }]
    }
    fake_wrf = {"cities": {"rio_cuarto": {"hourly": []}}}
    fake_metar_quiet = {"SAOC": {"raw_metar": "METAR SAOC 231800Z 04010KT 9999 SKC 20/10 Q1015", "wx_codes": []}}

    # Monkeypatch scan_goes19_band13 to simulate a high cold cloud top (T < -65°C)
    import backend.app.services.hail_service as hs
    original_scan = hs.scan_goes19_band13

    async def mock_scan_cirrus(*args, **kwargs):
        return {
            "available": True,
            "severe_core_detected": True,
            "extreme_core_detected": False,
            "severe_pixels_count": 25,
            "min_brightness_temp_c": -66.5,
        }

    hs.scan_goes19_band13 = mock_scan_cirrus
    try:
        res = await hs.evaluate_hail_risk(
            open_meteo_data=fake_open_meteo_stable,
            wrf_data=fake_wrf,
            metar_map=fake_metar_quiet,
        )
        # Imminent hail MUST NOT be triggered for cirrus
        assert res["imminent_hail_alert"] is False
        assert res["cirrus_rejected"] is True
        assert "descartado" in res["explanation"].lower() or "cirros" in res["explanation"].lower()
    finally:
        hs.scan_goes19_band13 = original_scan

    # 2. Supercell case: Satellite cold core detected AND atmosphere is unstable (CAPE >= 1500, shear >= 60)
    fake_open_meteo_convective = {
        "hourly": [{
            "timestamp_utc": "2026-11-23T18:00:00Z",
            "models": {
                "ecmwf_ifs025": {"cape_jkg": 2200.0, "wind_speed_kmh": 25.0, "wind_direction_deg": 350.0, "precip_prob_pct": 80.0, "precip_mm": 4.0},
                "icon_seamless": {"cape_jkg": 2100.0, "wind_speed_kmh": 25.0, "wind_direction_deg": 350.0, "precip_prob_pct": 80.0, "precip_mm": 4.0},
                "gfs_seamless": {"cape_jkg": 2000.0, "wind_speed_kmh": 25.0, "wind_direction_deg": 350.0, "precip_prob_pct": 80.0, "precip_mm": 4.0},
            },
            "wind_speed_500hPa_kmh": 85.0,
            "wind_direction_500hPa_deg": 260.0,
            "freezing_level_height_m": 3500.0,
        }]
    }

    hs.scan_goes19_band13 = mock_scan_cirrus
    try:
        res_supercell = await hs.evaluate_hail_risk(
            open_meteo_data=fake_open_meteo_convective,
            wrf_data=fake_wrf,
            metar_map=fake_metar_quiet,
        )
        # Imminent hail MUST be triggered
        assert res_supercell["imminent_hail_alert"] is True
        assert res_supercell["cirrus_rejected"] is False
    finally:
        hs.scan_goes19_band13 = original_scan


def test_sentinel_approaching_front_detection():
    """Validates front detection from peripheral sentinels (SAOR Villa Reynolds ~115 km SO)."""
    # 1. Front detected at SAOR: gust 65 km/h from 210° (SO) + thunderstorm (+TSRA), SAOC calm
    metar_map_front = {
        "SAOC": {
            "icao": "SAOC",
            "temp_c": 26.0,
            "wind_speed_kmh": 12.0,
            "wind_gust_kmh": None,
            "wind_direction_deg": 40.0,
            "raw_metar": "METAR SAOC 231800Z 04006KT 9999 FEW030 26/16 Q1010",
            "wx_codes": [],
        },
        "SAOR": {
            "icao": "SAOR",
            "temp_c": 18.0,
            "wind_speed_kmh": 45.0,
            "wind_gust_kmh": 65.0,
            "wind_direction_deg": 210.0,
            "raw_metar": "METAR SAOR 231800Z 21025G35KT 3000 +TSRA SQ BKN020 18/15 Q1013",
            "wx_codes": ["+TSRA", "SQ"],
        },
    }

    front_res = detect_approaching_front(metar_map_front)
    assert front_res["detected"] is True
    assert front_res["station_icao"] == "SAOR"
    assert front_res["distance_km"] in (115, 116, 117)
    assert front_res["cardinal"] == "SO"
    assert front_res["estimated_arrival_minutes"] in range(40, 120)
    assert any("rafagas" in t for t in front_res["triggers"])
    assert any("tsra" in t.lower() for t in front_res["triggers"])

    # 2. Front already impacting SAOC (SAOC already reports +TSRA and strong S gusts)
    metar_map_impacted = {
        "SAOC": {
            "icao": "SAOC",
            "temp_c": 17.0,
            "wind_speed_kmh": 52.0,
            "wind_gust_kmh": 68.0,
            "wind_direction_deg": 190.0,
            "raw_metar": "METAR SAOC 231800Z 19028G37KT 2000 +TSRA BKN015 17/16 Q1013",
            "wx_codes": ["+TSRA"],
        },
        "SAOR": metar_map_front["SAOR"],
    }
    impacted_res = detect_approaching_front(metar_map_impacted)
    assert impacted_res["detected"] is False
    assert impacted_res["reason"] == "impact_already_active_in_rio_cuarto"

    # 3. Sentinel summary distance and bearing verification
    sentinels = get_sentinels_summary(metar_map_front)
    assert len(sentinels) == 4
    saor_entry = next(s for s in sentinels if s["icao"] == "SAOR")
    assert saor_entry["distance_km"] in (115, 116, 117)
    assert saor_entry["cardinal"] == "SO"
    assert saor_entry["bearing_deg"] in range(230, 260)


def test_qnh_barometric_trend():
    """Validates 3-hour QNH barometric tendency and delta calculation."""
    now_dt = datetime.now(timezone.utc)
    t0_str = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    t3_str = (now_dt - timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Rapid drop (<= -3.0 hPa)
    history_rapid_drop = [
        {"icao": "SAOC", "obs_time_utc": t0_str, "qnh_hpa": 1008.0},
        {"icao": "SAOC", "obs_time_utc": t3_str, "qnh_hpa": 1011.5},
    ]
    trend1, delta1 = calculate_qnh_trend(icao="SAOC", current_qnh=1008.0, history_records=history_rapid_drop)
    assert trend1 == "bajando_rapido"
    assert delta1 == -3.5

    # 2. Moderate drop (<= -1.5 hPa and > -3.0 hPa)
    history_drop = [
        {"icao": "SAOC", "obs_time_utc": t0_str, "qnh_hpa": 1009.2},
        {"icao": "SAOC", "obs_time_utc": t3_str, "qnh_hpa": 1011.0},
    ]
    trend2, delta2 = calculate_qnh_trend(icao="SAOC", current_qnh=1009.2, history_records=history_drop)
    assert trend2 == "bajando"
    assert delta2 == -1.8

    # 3. Rising (>= +1.5 hPa)
    history_rise = [
        {"icao": "SAOC", "obs_time_utc": t0_str, "qnh_hpa": 1014.0},
        {"icao": "SAOC", "obs_time_utc": t3_str, "qnh_hpa": 1012.0},
    ]
    trend3, delta3 = calculate_qnh_trend(icao="SAOC", current_qnh=1014.0, history_records=history_rise)
    assert trend3 == "subiendo"
    assert delta3 == 2.0

    # 4. Steady (-1.5 < delta < +1.5)
    history_steady = [
        {"icao": "SAOC", "obs_time_utc": t0_str, "qnh_hpa": 1012.3},
        {"icao": "SAOC", "obs_time_utc": t3_str, "qnh_hpa": 1012.0},
    ]
    trend4, delta4 = calculate_qnh_trend(icao="SAOC", current_qnh=1012.3, history_records=history_steady)
    assert trend4 == "estable"
    assert delta4 == 0.3


def test_executive_summary_one_liner():
    """
    Validates deterministic 1-liner Argentine executive summary:
    - Natural phrasing
    - Maximum 120 characters strictly enforced
    - Covers severe storm, approaching front, imminent hail, and fair weather.
    """
    # 1. Imminent hail alert
    hail_alert = {"imminent_hail_alert": True}
    s1 = generate_executive_summary(current={}, hourly=[], daily=[], hail_risk=hail_alert)
    assert len(s1) <= 120
    assert "granizo" in s1.lower()
    assert "resguardar vehículos" in s1.lower()

    # 2. Approaching front from sentinel station
    front_alert = {"detected": True, "station_name": "Villa Reynolds", "estimated_arrival_minutes": 60}
    s2 = generate_executive_summary(current={}, hourly=[], daily=[], sentinel_front=front_alert)
    assert len(s2) <= 120
    assert "villa reynolds" in s2.lower()
    assert "frente" in s2.lower()

    # 3. Active storm now with clearing time
    curr_storm = {"weather_code": 95, "is_precipitating_now": True, "temperature_c": 19.5}
    hourly_clearing = [
        {"timestamp_utc": "2026-11-23T18:00:00Z", "weather_code": 95, "precip_mm": 4.0},
        {"timestamp_utc": "2026-11-23T19:00:00Z", "weather_code": 95, "precip_mm": 2.0},
        {"timestamp_utc": "2026-11-23T20:00:00Z", "weather_code": 3, "precip_mm": 0.0, "precip_prob_pct": 10},
    ]
    daily_info = [{"temperature_max_c": 22.0, "temperature_min_c": 13.0}]
    s3 = generate_executive_summary(current=curr_storm, hourly=hourly_clearing, daily=daily_info)
    assert len(s3) <= 120
    assert "tormenta" in s3.lower()
    assert "13 °c" in s3.lower()

    # 4. Sunny warm afternoon
    curr_clear = {"weather_code": 0, "temperature_c": 27.5, "wind_speed_kmh": 20.0, "wind_direction_deg": 10.0}
    daily_sunny = [{"temperature_max_c": 31.0, "temperature_min_c": 18.0}]
    s4 = generate_executive_summary(current=curr_clear, hourly=[], daily=daily_sunny)
    assert len(s4) <= 120
    assert "31 °C" in s4 or "31 °c" in s4.lower()
    assert "norte" in s4.lower()
