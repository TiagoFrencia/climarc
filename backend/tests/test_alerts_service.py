import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
import pytest
from fastapi.testclient import TestClient

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import (
    init_db,
    get_alert_settings,
    update_alert_settings,
    get_alert_state,
    set_alert_state,
)
from backend.app.services.alerts_service import (
    is_in_quiet_hours,
    evaluate_alert_conditions,
    process_and_dispatch_alerts,
)
from backend.app.services.telegram_service import (
    send_telegram_alert,
    format_alert_message,
    CORDOBA_TZ,
)

client = TestClient(app)


@pytest.fixture(autouse=True)
def init_clean_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_alerts.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", test_db)
    init_db(test_db)
    yield test_db


def test_quiet_hours_overnight_and_daytime():
    """Verifies quiet hours logic for overnight ranges (23->07) and daytime ranges (07->14)."""
    # 1. Overnight range: 23 to 7
    dt_23 = datetime(2026, 11, 23, 23, 0, tzinfo=CORDOBA_TZ)
    dt_02 = datetime(2026, 11, 23, 2, 30, tzinfo=CORDOBA_TZ)
    dt_06 = datetime(2026, 11, 23, 6, 59, tzinfo=CORDOBA_TZ)
    dt_07 = datetime(2026, 11, 23, 7, 0, tzinfo=CORDOBA_TZ)
    dt_15 = datetime(2026, 11, 23, 15, 0, tzinfo=CORDOBA_TZ)
    dt_22 = datetime(2026, 11, 23, 22, 59, tzinfo=CORDOBA_TZ)

    assert is_in_quiet_hours(dt_23, start_hour=23, end_hour=7, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_02, start_hour=23, end_hour=7, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_06, start_hour=23, end_hour=7, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_07, start_hour=23, end_hour=7, quiet_enabled=True) is False
    assert is_in_quiet_hours(dt_15, start_hour=23, end_hour=7, quiet_enabled=True) is False
    assert is_in_quiet_hours(dt_22, start_hour=23, end_hour=7, quiet_enabled=True) is False

    # 2. Daytime range: 7 to 14
    dt_day_06 = datetime(2026, 11, 23, 6, 30, tzinfo=CORDOBA_TZ)
    dt_day_07 = datetime(2026, 11, 23, 7, 0, tzinfo=CORDOBA_TZ)
    dt_day_10 = datetime(2026, 11, 23, 10, 0, tzinfo=CORDOBA_TZ)
    dt_day_13 = datetime(2026, 11, 23, 13, 59, tzinfo=CORDOBA_TZ)
    dt_day_14 = datetime(2026, 11, 23, 14, 0, tzinfo=CORDOBA_TZ)

    assert is_in_quiet_hours(dt_day_06, start_hour=7, end_hour=14, quiet_enabled=True) is False
    assert is_in_quiet_hours(dt_day_07, start_hour=7, end_hour=14, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_day_10, start_hour=7, end_hour=14, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_day_13, start_hour=7, end_hour=14, quiet_enabled=True) is True
    assert is_in_quiet_hours(dt_day_14, start_hour=7, end_hour=14, quiet_enabled=True) is False

    # 3. Disabled quiet hours: always False
    assert is_in_quiet_hours(dt_02, start_hour=23, end_hour=7, quiet_enabled=False) is False
    assert is_in_quiet_hours(dt_day_10, start_hour=7, end_hour=14, quiet_enabled=False) is False


def test_priority_1_ignores_quiet_hours(init_clean_db):
    """Priority 1 (e.g. thunderstorm & hail in METAR) must fire even during quiet hours."""
    db_path = init_clean_db

    metar_obs = {
        "icao": "SAOC",
        "raw_metar": "METAR SAOC 230400Z 18030G50KT +TSRA GR BKN015CB 21/18 Q1006",
        "wx_codes": ["+TSRA", "GR"],
        "temp_c": 21.0,
        "wind_speed_kmh": 55.0,
        "wind_gust_kmh": 92.0,
        "qnh_hpa": 1006.0,
    }

    alert_settings = {
        "quiet_enabled": True,
        "quiet_start_hour": 23,
        "quiet_end_hour": 7,
        "cooldown_hours": 4.0,
    }

    # Evaluate at 03:00 AM Cordoba (inside quiet hours)
    now_cordoba = datetime(2026, 11, 23, 3, 0, tzinfo=CORDOBA_TZ)
    now_utc = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc)

    detected = evaluate_alert_conditions(
        city_key="rio_cuarto",
        alert_settings=alert_settings,
        metar_obs=metar_obs,
    )

    p1_alerts = [a for a in detected if a["priority"] == 1]
    assert len(p1_alerts) >= 2  # granizo and tormenta
    types = [a["type"] for a in p1_alerts]
    assert "granizo" in types
    assert "tormenta" in types

    async def _run():
        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 999}),
        ) as mock_send:
            result = await process_and_dispatch_alerts(
                detected_alerts=detected,
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=now_utc,
                now_cordoba=now_cordoba,
            )

            assert result["sent_count"] >= 2
            # Priority 1 must not be omitted by quiet hours
            omitted_quiet = [o for o in result["omitted"] if o.get("reason") == "quiet_hours"]
            assert len(omitted_quiet) == 0
            assert mock_send.call_count >= 2

    asyncio.run(_run())


def test_priority_2_respects_quiet_hours(init_clean_db):
    """Priority 2 (preventive: imminent rain, gusts 55 km/h) is omitted during quiet hours."""
    db_path = init_clean_db

    open_meteo_hourly = [
        {"models": {"ecmwf_ifs025": {"precip_prob_pct": 85.0, "precip_mm": 2.5, "wind_gust_kmh": 55.0}}},
        {"models": {"ecmwf_ifs025": {"precip_prob_pct": 70.0, "precip_mm": 1.8, "wind_gust_kmh": 50.0}}},
    ]

    alert_settings = {
        "rain_prob_pct": 60.0,
        "rain_mm": 1.5,
        "wrf_nearby_mm": 8.0,
        "gust_kmh": 45.0,
        "cooldown_hours": 4.0,
        "quiet_enabled": True,
        "quiet_start_hour": 23,
        "quiet_end_hour": 7,
    }

    # At 03:00 AM Cordoba (inside quiet hours)
    now_cordoba = datetime(2026, 11, 23, 3, 0, tzinfo=CORDOBA_TZ)
    now_utc = datetime(2026, 11, 23, 6, 0, tzinfo=timezone.utc)

    detected = evaluate_alert_conditions(
        city_key="rio_cuarto",
        alert_settings=alert_settings,
        open_meteo_hourly=open_meteo_hourly,
    )

    p2_alerts = [a for a in detected if a["priority"] == 2]
    assert len(p2_alerts) >= 2  # rain:lluvia_inminente and wind:rafagas_fuertes

    async def _run():
        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 100}),
        ) as mock_send:
            result = await process_and_dispatch_alerts(
                detected_alerts=detected,
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=now_utc,
                now_cordoba=now_cordoba,
            )

            assert result["sent_count"] == 0
            assert result["omitted_count"] >= 2
            for o in result["omitted"]:
                assert o["reason"] == "quiet_hours"
            mock_send.assert_not_called()

    asyncio.run(_run())


def test_anti_spam_cooldown_and_escalation_bypass(init_clean_db):
    """
    Verifies anti-spam cooldown prevents duplicate sends during cooldown,
    and escalations (e.g. amarillo -> naranja) immediately bypass cooldown.
    """
    db_path = init_clean_db
    alert_settings = {
        "cooldown_hours": 4.0,
        "quiet_enabled": False,
    }

    alert_base = {
        "priority": 2,
        "level": "amarillo",
        "type": "viento_fuerte",
        "alert_key": "wind:rafagas_fuertes",
        "title": "Aviso de ráfagas de viento fuertes",
        "description": "Ráfagas previstas de 55 km/h.",
    }

    t0_utc = datetime(2026, 11, 23, 12, 0, tzinfo=timezone.utc)
    t0_cba = datetime(2026, 11, 23, 9, 0, tzinfo=CORDOBA_TZ)

    async def _run():
        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 1}),
        ):
            # 1. First send at T0: must be sent
            res0 = await process_and_dispatch_alerts(
                detected_alerts=[alert_base],
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=t0_utc,
                now_cordoba=t0_cba,
            )
            assert res0["sent_count"] == 1
            assert res0["omitted_count"] == 0

            # Verify state in SQLite
            st = get_alert_state("rio_cuarto", "wind:rafagas_fuertes", custom_path=db_path)
            assert st is not None
            assert st["last_severity"] == "amarillo"

        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 2}),
        ) as mock_send_t1:
            # 2. Resend after 1 hour (T1 = T0 + 1h, within 4h cooldown): must be omitted by cooldown
            t1_utc = t0_utc + timedelta(hours=1)
            t1_cba = t0_cba + timedelta(hours=1)

            res1 = await process_and_dispatch_alerts(
                detected_alerts=[alert_base],
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=t1_utc,
                now_cordoba=t1_cba,
            )
            assert res1["sent_count"] == 0
            assert res1["omitted_count"] == 1
            assert res1["omitted"][0]["reason"] == "cooldown"
            mock_send_t1.assert_not_called()

        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 3}),
        ) as mock_send_t2:
            # 3. Severity escalates to NARANJA after 1.5 hours: BYPASSES cooldown!
            t2_utc = t0_utc + timedelta(hours=1, minutes=30)
            t2_cba = t0_cba + timedelta(hours=1, minutes=30)

            alert_escalated = {
                "priority": 1,
                "level": "naranja",
                "type": "viento_fuerte",
                "alert_key": "wind:rafagas_fuertes",
                "title": "Alerta por viento extremo",
                "description": "Ráfagas aumentaron a 85 km/h.",
            }

            res2 = await process_and_dispatch_alerts(
                detected_alerts=[alert_escalated],
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=t2_utc,
                now_cordoba=t2_cba,
            )
            assert res2["sent_count"] == 1
            assert res2["omitted_count"] == 0
            mock_send_t2.assert_called_once()

            st2 = get_alert_state("rio_cuarto", "wind:rafagas_fuertes", custom_path=db_path)
            assert st2["last_severity"] == "naranja"

        with patch(
            "backend.app.services.alerts_service.send_telegram_alert",
            new=AsyncMock(return_value={"sent": True, "message_id": 4}),
        ) as mock_send_t3:
            # 4. After 5 hours (T3 = T2 + 5h > 4h cooldown): sends normally again
            t3_utc = t2_utc + timedelta(hours=5)
            t3_cba = t2_cba + timedelta(hours=5)

            res3 = await process_and_dispatch_alerts(
                detected_alerts=[alert_escalated],
                city_key="rio_cuarto",
                alert_settings=alert_settings,
                custom_db_path=db_path,
                now_utc=t3_utc,
                now_cordoba=t3_cba,
            )
            assert res3["sent_count"] == 1
            assert res3["omitted_count"] == 0
            mock_send_t3.assert_called_once()

    asyncio.run(_run())


def test_security_cron_secret(monkeypatch):
    """POST /api/alerts/check must strictly require X-CRON-SECRET matching CRON_SECRET."""
    monkeypatch.setattr(settings, "CRON_SECRET", "super-secret-cron-123")

    # 1. No header -> 401
    resp_no_header = client.post("/api/alerts/check")
    assert resp_no_header.status_code == 401

    # 2. Invalid header -> 401
    resp_bad_header = client.post("/api/alerts/check", headers={"X-CRON-SECRET": "wrong-secret"})
    assert resp_bad_header.status_code == 401

    # 3. Valid header -> 200 (mocking check_and_dispatch_cron)
    with patch(
        "backend.app.main.check_and_dispatch_cron",
        new=AsyncMock(return_value={"status": "ok", "detected": [], "sent": [], "omitted": []}),
    ):
        resp_ok = client.post("/api/alerts/check", headers={"X-CRON-SECRET": "super-secret-cron-123"})
        assert resp_ok.status_code == 200
        assert resp_ok.json()["status"] == "ok"


def test_security_admin_pin_protection(monkeypatch):
    """PUT /api/alerts/settings and POST /api/alerts/test must enforce X-ADMIN-PIN."""
    monkeypatch.setattr(settings, "ADMIN_PIN", "9876")

    # 1. Test PUT settings with missing/bad pin
    resp_put_no_pin = client.put("/api/alerts/settings", json={"rain_prob_pct": 75.0})
    assert resp_put_no_pin.status_code == 401

    resp_put_bad_pin = client.put(
        "/api/alerts/settings",
        headers={"X-ADMIN-PIN": "0000"},
        json={"rain_prob_pct": 75.0},
    )
    assert resp_put_bad_pin.status_code == 401

    resp_put_ok = client.put(
        "/api/alerts/settings",
        headers={"X-ADMIN-PIN": "9876"},
        json={"rain_prob_pct": 75.0},
    )
    assert resp_put_ok.status_code == 200
    assert resp_put_ok.json()["settings"]["rain_prob_pct"] == 75.0

    # 2. Test POST /api/alerts/test with pin
    resp_test_no_pin = client.post("/api/alerts/test")
    assert resp_test_no_pin.status_code == 401

    with patch(
        "backend.app.main.send_telegram_alert",
        new=AsyncMock(return_value={"sent": True, "message_id": 555}),
    ):
        resp_test_ok = client.post("/api/alerts/test", headers={"X-ADMIN-PIN": "9876"})
        assert resp_test_ok.status_code == 200
        assert resp_test_ok.json()["result"]["sent"] is True

    # 3. Local dev fallback: ADMIN_PIN empty allows with warning
    monkeypatch.setattr(settings, "ADMIN_PIN", "")
    resp_empty_pin = client.put("/api/alerts/settings", json={"rain_mm": 2.0})
    assert resp_empty_pin.status_code == 200


def test_security_never_leak_telegram_credentials(monkeypatch):
    """
    CRITICAL SECURITY AUDIT:
    GET /api/alerts/settings MUST NEVER expose TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.
    """
    secret_token = "123456789:SECRET_BOT_TOKEN_DO_NOT_LEAK"
    secret_chat = "-1009988776655"
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", secret_token)
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", secret_chat)

    resp = client.get("/api/alerts/settings")
    assert resp.status_code == 200
    data = resp.json()

    assert data["telegram_configured"] is True
    assert "settings" in data
    assert "recent_alerts" in data

    # Verify secret token/chat are nowhere in the response text
    raw_response_text = resp.text
    assert secret_token not in raw_response_text
    assert secret_chat not in raw_response_text
    assert "telegram_bot_token" not in raw_response_text.lower()
    assert "telegram_chat_id" not in raw_response_text.lower()
    assert "token" not in data
    assert "chat_id" not in data


def test_telegram_service_unconfigured_graceful(monkeypatch):
    """If Telegram credentials are not set, send_telegram_alert returns gracefully without exceptions."""
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", "")

    result = asyncio.run(send_telegram_alert("<b>Test message</b>"))
    assert result == {"sent": False, "reason": "telegram_not_configured"}
