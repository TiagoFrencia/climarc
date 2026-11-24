import os
import sys
import math
import subprocess
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import (
    init_db,
    save_forecast_snapshots,
    save_metar_record,
    get_model_accuracy_leaderboard,
)
from backend.app.services.alerts_service import generate_daily_briefing_html
from backend.app.services.ensemble_engine import (
    generate_executive_summary,
    clamp_and_renormalize_weights,
    get_adaptive_weights_state,
    generate_ensemble_forecast,
    CORDOBA_TZ,
)
from backend.app.services.satellite_image_service import generate_goes19_telegram_image
from backend.app.services.metar_service import calculate_qnh_trend

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_test_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_adaptive.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", test_db)
    init_db(test_db)
    yield test_db


@pytest.mark.asyncio
async def test_briefing_reads_real_hail_and_active_alerts(setup_test_db):
    """
    PASO 0.1: Briefing must read exact hail_risk (level, score, peak_window)
    and active alerts, never defaulting to SIN RIESGO or 'Sin alertas activas'.
    """
    mock_hail = {
        "level": "moderado",
        "score": 45.0,
        "peak_window_local": "21:00 - 23:00",
        "cape_max": 1850.0,
        "shear_0_6km_kmh": 42.0,
        "freezing_level_m": 3800.0,
    }
    mock_alerts = [
        {
            "priority": 1,
            "level": "naranja",
            "type": "alerta_smn_naranja",
            "title": "Alerta Naranja por Tormentas Fuertes",
            "description": "Se prevén tormentas localmente severas con abundante caída de agua.",
        }
    ]

    # Save metar history with +1.0 hPa delta (should say Estable)
    now_utc = datetime.now(timezone.utc)
    t_3h_ago = (now_utc - timedelta(hours=3)).strftime("%Y-%m-%dT%H:00:00Z")
    t_now = now_utc.strftime("%Y-%m-%dT%H:00:00Z")
    save_metar_record(icao="SAOC", obs_time_utc=t_3h_ago, temp_c=20.0, wind_kmh=10.0, qnh_hpa=1012.0, raw_metar="METAR SAOC", custom_path=setup_test_db)
    save_metar_record(icao="SAOC", obs_time_utc=t_now, temp_c=19.0, wind_kmh=12.0, qnh_hpa=1013.0, raw_metar="METAR SAOC", custom_path=setup_test_db)

    briefing_html = await generate_daily_briefing_html(
        city_key="rio_cuarto",
        custom_db_path=setup_test_db,
        hail_risk=mock_hail,
        active_alerts=mock_alerts,
    )

    # Must contain MODERADO (Score: 45/100) and peak window
    assert "MODERADO" in briefing_html
    assert "45/100" in briefing_html
    assert "21:00 - 23:00" in briefing_html

    # Must contain active alert title and not say "Sin alertas activas"
    assert "Alerta Naranja" in briefing_html
    assert "Sin alertas activas" not in briefing_html

    # QNH tendency threshold: +1.0 hPa is within (-1.5, +1.5) -> must say Estable
    assert "Estable (+1.0 hPa/3h)" in briefing_html or "Estable" in briefing_html
    assert "Subiendo" not in briefing_html


def test_qnh_trend_unified_thresholds(setup_test_db):
    """
    PASO 0.1: Unify QNH tendency thresholds (>= +1.5 Subiendo, <= -1.5 Bajando, intermediate Estable).
    """
    now_utc = datetime.now(timezone.utc)
    t_3h = (now_utc - timedelta(hours=3)).strftime("%Y-%m-%dT%H:00:00Z")
    t_now = now_utc.strftime("%Y-%m-%dT%H:00:00Z")

    # +1.0 hPa -> Estable
    h_stable = [
        {"icao": "SAOC", "obs_time_utc": t_3h, "qnh_hpa": 1010.0},
        {"icao": "SAOC", "obs_time_utc": t_now, "qnh_hpa": 1011.0},
    ]
    trend, delta = calculate_qnh_trend(icao="SAOC", current_qnh=1011.0, history_records=h_stable)
    assert trend == "estable"
    assert delta == 1.0

    # +1.8 hPa -> Subiendo
    h_rising = [
        {"icao": "SAOC", "obs_time_utc": t_3h, "qnh_hpa": 1010.0},
        {"icao": "SAOC", "obs_time_utc": t_now, "qnh_hpa": 1011.8},
    ]
    trend_r, delta_r = calculate_qnh_trend(icao="SAOC", current_qnh=1011.8, history_records=h_rising)
    assert trend_r == "subiendo"
    assert delta_r == 1.8

    # -2.0 hPa -> Bajando
    h_falling = [
        {"icao": "SAOC", "obs_time_utc": t_3h, "qnh_hpa": 1012.0},
        {"icao": "SAOC", "obs_time_utc": t_now, "qnh_hpa": 1010.0},
    ]
    trend_f, delta_f = calculate_qnh_trend(icao="SAOC", current_qnh=1010.0, history_records=h_falling)
    assert trend_f == "bajando"
    assert delta_f == -2.0


def test_nighttime_executive_summary_coherence():
    """
    PASO 0.2: At night (>= 20:00 local), summary must never say 'Tarde templada...'.
    If severe alert is active, it must never say 'cielo despejado'.
    """
    curr = {"temperature_c": 16.0, "weather_code": 0, "is_precipitating_now": False, "wind_speed_kmh": 12.0}
    daily = [{"temperature_max_c": 26.0}, {"temperature_max_c": 23.0}]
    hourly = []

    # Mock local time to 22:30 Cordoba
    dt_night = datetime(2026, 11, 23, 22, 30, tzinfo=CORDOBA_TZ)
    with patch("backend.app.services.ensemble_engine.datetime") as mock_dt:
        mock_dt.now.return_value = dt_night
        mock_dt.fromisoformat = datetime.fromisoformat

        # 1. Normal stable night
        s_night = generate_executive_summary(current=curr, hourly=hourly, daily=daily)
        assert not s_night.startswith("Tarde templada")
        assert not s_night.startswith("Tarde soleada")
        assert "Noche" in s_night
        assert "mañana máx. 23 °C" in s_night or "mañana" in s_night

        # 2. Night with active naranja alert -> must not say "cielo despejado"
        alerts = [{"level": "naranja", "title": "Alerta por tormentas"}]
        s_alert = generate_executive_summary(current=curr, hourly=hourly, daily=daily, alerts=alerts)
        assert "despejado" not in s_alert
        assert "alerta" in s_alert.lower() or "inestable" in s_alert.lower()

        # 3. Night with hail risk >= moderado -> must not say "cielo despejado"
        hail = {"level": "moderado", "score": 50.0}
        s_hail = generate_executive_summary(current=curr, hourly=hourly, daily=daily, hail_risk=hail)
        assert "despejado" not in s_hail


@pytest.mark.asyncio
async def test_satellite_calibration_source():
    """
    PASO 0.5: Minimum cloud top temp in /satelite must come strictly from scan_goes19_band13.
    """
    mock_scan = {
        "available": True,
        "min_brightness_temp_c": -64.2,
        "center_brightness_temp_c": -58.0,
        "severe_core_detected": True,
    }
    with patch("backend.app.services.satellite_image_service.scan_goes19_band13", new=AsyncMock(return_value=mock_scan)):
        img_bytes, min_t, time_str, conv_status = await generate_goes19_telegram_image()
        assert min_t == -64.2
        assert "Convección severa" in conv_status
        assert len(img_bytes) > 1000


def test_clamping_and_renormalize_weights():
    """
    PASO 2: Weights must sum to 1.0 and be strictly bounded to [0.08, 0.45].
    """
    # Extreme disparity: one super model (MAE 0.1), one terrible (MAE 10.0)
    raw = {
        "wrf_smn": 1.0 / ((0.1 + 0.35) ** 2),   # huge
        "ecmwf_ifs025": 1.0 / ((0.2 + 0.35) ** 2),
        "icon_seamless": 1.0 / ((4.0 + 0.35) ** 2),
        "gfs_seamless": 1.0 / ((10.0 + 0.35) ** 2), # tiny
    }
    clamped = clamp_and_renormalize_weights(raw, min_w=0.08, max_w=0.45)

    assert abs(sum(clamped.values()) - 1.0) < 1e-3
    for k, v in clamped.items():
        assert 0.08 <= v <= 0.45, f"Model {k} weight {v} outside [0.08, 0.45]"


def test_adaptive_weights_safe_gate(setup_test_db):
    """
    PASO 2: Fallback to static base weights if < 3 models have >= 48 samples.
    Activates adaptive weights when >= 3 models have >= 48 samples.
    """
    now_utc = datetime.now(timezone.utc)

    # 1. Only 10 samples -> Must NOT activate adaptive weights
    snapshots = []
    for i in range(10):
        t_str = (now_utc - timedelta(hours=i + 1)).strftime("%Y-%m-%dT%H:00:00Z")
        save_metar_record(icao="SAOC", obs_time_utc=t_str, temp_c=20.0, wind_kmh=15.0, qnh_hpa=1012.0, raw_metar="METAR SAOC", custom_path=setup_test_db)
        for m in ["wrf_smn", "ecmwf_ifs025", "icon_seamless", "gfs_seamless"]:
            snapshots.append({
                "target_time_utc": t_str,
                "predicted_temp_c": 21.0,
                "predicted_wind_kmh": 14.0,
                "predicted_precip_mm": 0.0,
                "model_source": m,
            })
    save_forecast_snapshots(city="rio_cuarto", snapshots=snapshots, custom_path=setup_test_db)

    state_insufficient = get_adaptive_weights_state(
        city_key="rio_cuarto",
        icao="SAOC",
        force_refresh=True,
        custom_path=setup_test_db,
    )
    assert state_insufficient["adaptive_weights_active"] is False
    assert state_insufficient["current_weights"]["wrf_smn"] == 0.35

    # 2. Add 40 more samples (total 50 >= 48 samples for 4 models) -> Must activate!
    more_snapshots = []
    for i in range(10, 50):
        t_str = (now_utc - timedelta(hours=i + 1)).strftime("%Y-%m-%dT%H:00:00Z")
        save_metar_record(icao="SAOC", obs_time_utc=t_str, temp_c=20.0, wind_kmh=15.0, qnh_hpa=1012.0, raw_metar="METAR SAOC", custom_path=setup_test_db)
        # Give WRF low error, GFS higher error
        more_snapshots.append({"target_time_utc": t_str, "predicted_temp_c": 20.2, "predicted_wind_kmh": 15.0, "model_source": "wrf_smn"})
        more_snapshots.append({"target_time_utc": t_str, "predicted_temp_c": 20.5, "predicted_wind_kmh": 15.0, "model_source": "ecmwf_ifs025"})
        more_snapshots.append({"target_time_utc": t_str, "predicted_temp_c": 21.0, "predicted_wind_kmh": 15.0, "model_source": "icon_seamless"})
        more_snapshots.append({"target_time_utc": t_str, "predicted_temp_c": 23.0, "predicted_wind_kmh": 15.0, "model_source": "gfs_seamless"})
    save_forecast_snapshots(city="rio_cuarto", snapshots=more_snapshots, custom_path=setup_test_db)

    state_sufficient = get_adaptive_weights_state(
        city_key="rio_cuarto",
        icao="SAOC",
        force_refresh=True,
        custom_path=setup_test_db,
    )
    assert state_sufficient["adaptive_weights_active"] is True
    weights = state_sufficient["current_weights"]
    assert abs(sum(weights.values()) - 1.0) < 1e-3
    for k, v in weights.items():
        assert 0.08 <= v <= 0.45
    # WRF had lower error than GFS, so its weight must be higher than GFS
    assert weights["wrf_smn"] > weights["gfs_seamless"]


def test_api_accuracy_endpoint(setup_test_db):
    """
    PASO 1: GET /api/accuracy?city=rio_cuarto&days=7 returns leaderboard and adaptive engine status.
    """
    res = client.get("/api/accuracy?city=rio_cuarto&days=7")
    assert res.status_code == 200
    data = res.json()

    assert "leaderboard" in data
    assert "adaptive_weights_active" in data
    assert data["min_samples_required"] == 48
    assert "current_weights" in data
    assert "progress_samples" in data
    assert isinstance(data["leaderboard"], list)


def test_retrain_script_safeguard_clean_exit():
    """
    PASO 4: scripts/retrain_from_neon.py exits cleanly with code 0 if < 500 samples.
    """
    script_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "scripts", "retrain_from_neon.py")
    res = subprocess.run([sys.executable, script_path], capture_output=True, text=True)
    assert res.returncode == 0
    assert "DATABASE_URL no configurada" in res.stdout or "horas acumuladas" in res.stdout
