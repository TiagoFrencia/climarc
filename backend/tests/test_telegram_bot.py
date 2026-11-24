import io
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app.main import app
from backend.app.config import settings
from backend.app.database import (
    init_db,
    get_alert_state,
    set_alert_state,
    update_alert_settings,
    get_alert_settings,
)
from backend.app.services.telegram_service import CORDOBA_TZ
from backend.app.services.telegram_bot_handler import (
    build_ahora_response,
    build_noche_response,
    build_granizo_response,
    build_ruta_response,
    build_ayuda_response,
    is_update_processed,
    mark_update_processed,
    handle_telegram_message_background,
)
from backend.app.services.satellite_image_service import (
    generate_goes19_telegram_image,
)
from backend.app.services.alerts_service import (
    check_and_dispatch_cron,
    generate_daily_briefing_html,
)

client = TestClient(app)

TEST_SECRET = "test-secret-token-32-chars-long"
TEST_CHAT_ID = "998877665"
TEST_PIN = "9876"


@pytest.fixture(autouse=True)
def setup_env_and_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_bot.db")
    monkeypatch.setattr(settings, "DATABASE_PATH", test_db)
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_SECRET", TEST_SECRET)
    monkeypatch.setattr(settings, "TELEGRAM_CHAT_ID", TEST_CHAT_ID)
    monkeypatch.setattr(settings, "TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setattr(settings, "ADMIN_PIN", TEST_PIN)
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://climarc.vercel.app")
    init_db(test_db)
    yield test_db


# ---------------------------------------------------------------------------
# 1. Seguridad: Validación de Secret Token en Webhook
# ---------------------------------------------------------------------------
def test_webhook_rejects_missing_secret():
    """Requests without X-Telegram-Bot-Api-Secret-Token must be rejected with 401."""
    resp = client.post("/api/telegram/webhook", json={"update_id": 1})
    assert resp.status_code == 401
    assert "X-Telegram-Bot-Api-Secret-Token" in resp.json().get("detail", "")


def test_webhook_rejects_invalid_secret():
    """Requests with wrong secret token must be rejected with 401."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": "wrong-secret-token"}
    resp = client.post("/api/telegram/webhook", json={"update_id": 2}, headers=headers)
    assert resp.status_code == 401


def test_webhook_accepts_valid_secret():
    """Requests with valid secret token and valid chat_id succeed with 200 OK."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET}
    payload = {
        "update_id": 1001,
        "message": {
            "chat": {"id": int(TEST_CHAT_ID)},
            "text": "/ayuda",
        },
    }
    with patch("backend.app.main.handle_telegram_message_background"):
        resp = client.post("/api/telegram/webhook", json=payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json().get("ok") is True


# ---------------------------------------------------------------------------
# 2. Seguridad: Whitelist Estricta de Usuario
# ---------------------------------------------------------------------------
def test_webhook_silently_ignores_unauthorized_chat():
    """Updates from unauthorized chat_ids must return silent 200 OK without dispatching."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET}
    payload = {
        "update_id": 2001,
        "message": {
            "chat": {"id": 11223344},  # Unauthorized chat
            "text": "/ahora",
        },
    }
    with patch("backend.app.main.handle_telegram_message_background") as mock_handler:
        resp = client.post("/api/telegram/webhook", json=payload, headers=headers)
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "status": "ignored"}
        mock_handler.assert_not_called()


# ---------------------------------------------------------------------------
# 3. Deduplicación por update_id
# ---------------------------------------------------------------------------
def test_webhook_deduplication():
    """Subsequent updates with identical update_id must be identified as duplicate."""
    headers = {"X-Telegram-Bot-Api-Secret-Token": TEST_SECRET}
    payload = {
        "update_id": 3001,
        "message": {
            "chat": {"id": int(TEST_CHAT_ID)},
            "text": "/ayuda",
        },
    }
    with patch("backend.app.main.handle_telegram_message_background"):
        # First arrival: processed
        resp1 = client.post("/api/telegram/webhook", json=payload, headers=headers)
        assert resp1.status_code == 200
        assert resp1.json() == {"ok": True}

        # Second arrival (reintent by Render cold start / timeout): duplicate
        resp2 = client.post("/api/telegram/webhook", json=payload, headers=headers)
        assert resp2.status_code == 200
        assert resp2.json() == {"ok": True, "status": "duplicate"}


# ---------------------------------------------------------------------------
# 4. Comandos de Texto: /ahora, /noche, /granizo, /ruta, /ayuda
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cmd_ahora_response():
    resp_html = await build_ahora_response()
    assert "CLIMA AHORA" in resp_html
    assert "Río Cuarto" in resp_html
    assert "Temperatura" in resp_html
    assert "Presión" in resp_html
    assert "Pista aérea" in resp_html
    assert "https://climarc.vercel.app" in resp_html


@pytest.mark.asyncio
async def test_cmd_noche_response():
    resp_html = await build_noche_response()
    assert "PRONÓSTICO DE LA NOCHE" in resp_html
    assert "Mínima prevista" in resp_html
    assert "Recomendación" in resp_html


@pytest.mark.asyncio
async def test_cmd_granizo_response():
    resp_html = await build_granizo_response()
    assert "MÓDULO ANTIGRANIZO" in resp_html
    assert "Etapa 1: Potencial Atmosférico" in resp_html
    assert "Etapa 2: Escáner GOES-19" in resp_html
    assert "CAPE" in resp_html
    assert "Cizalladura" in resp_html


@pytest.mark.asyncio
async def test_cmd_ruta_response():
    resp_cordoba = await build_ruta_response("cordoba")
    assert "ESTADO DE RUTA" in resp_cordoba
    assert "RN 36" in resp_cordoba
    assert "Semáforo global" in resp_cordoba
    assert "Río Cuarto" in resp_cordoba
    assert "Córdoba Capital" in resp_cordoba

    resp_vm = await build_ruta_response("villamaria")
    assert "RN 158" in resp_vm
    assert "Villa María" in resp_vm


def test_cmd_ayuda_response():
    resp_html = build_ayuda_response()
    assert "/ahora" in resp_html
    assert "/noche" in resp_html
    assert "/granizo" in resp_html
    assert "/satelite" in resp_html
    assert "/ruta" in resp_html
    assert "/ayuda" in resp_html


# ---------------------------------------------------------------------------
# 5. Generación de Foto GOES-19 Real en Memoria (Pillow + BytesIO)
# ---------------------------------------------------------------------------
def _create_dummy_tile_bytes(color=(50, 50, 50, 255), mode="RGBA"):
    img = Image.new("RGBA", (256, 256), color)
    bio = io.BytesIO()
    if mode == "P":
        # Create indexed image to test palette handling
        p_img = Image.new("P", (256, 256), 64)
        p_img.putpalette([i % 256 for i in range(768)])
        p_img.save(bio, format="PNG")
    else:
        img.save(bio, format="PNG")
    bio.seek(0)
    return bio.getvalue()


@pytest.mark.asyncio
async def test_satellite_image_generation_in_memory():
    """Tests composite 512x512 image generation in memory mocking only tile HTTP calls."""
    base_tile_bytes = _create_dummy_tile_bytes((30, 30, 30, 255))
    sat_tile_bytes = _create_dummy_tile_bytes(mode="P")
    ref_tile_bytes = _create_dummy_tile_bytes((0, 0, 0, 0))

    class MockTileResponse:
        def __init__(self, content):
            self.status_code = 200
            self.content = content

        def json(self):
            return ["20261124.010000"]

    async def mock_get(url, **kwargs):
        if "api/times" in url:
            return MockTileResponse(b"")
        elif "World_Dark_Gray_Base" in url:
            return MockTileResponse(base_tile_bytes)
        elif "G19-ABI-FD-BAND13-GRAD" in url:
            return MockTileResponse(sat_tile_bytes)
        elif "World_Dark_Gray_Reference" in url:
            return MockTileResponse(ref_tile_bytes)
        return MockTileResponse(base_tile_bytes)

    mock_client = AsyncMock()
    mock_client.get = mock_get

    img_bytes, min_t, ts, conv = await generate_goes19_telegram_image(client=mock_client)

    # 1. Zero disk writes, non-empty PNG
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000
    assert img_bytes[:8] == b"\x89PNG\r\n\x1a\n"

    # 2. Dimensions check
    loaded_img = Image.open(io.BytesIO(img_bytes))
    assert loaded_img.size == (512, 512)

    # 3. Telemetry outputs
    assert isinstance(min_t, float)
    assert isinstance(ts, str)
    assert isinstance(conv, str)


@pytest.mark.asyncio
async def test_satellite_image_fallback_when_esri_fails():
    """Tests clean fallback to GOES-19 tiles alone when Esri fails with HTTP 500/timeout."""
    sat_tile_bytes = _create_dummy_tile_bytes(mode="P")

    class MockTileResponse:
        def __init__(self, code, content=b""):
            self.status_code = code
            self.content = content

        def json(self):
            return ["20261124.010000"]

    async def mock_get(url, **kwargs):
        if "api/times" in url:
            return MockTileResponse(200, b"")
        elif "G19-ABI-FD-BAND13-GRAD" in url:
            return MockTileResponse(200, sat_tile_bytes)
        else:
            # Simulate Esri failure
            return MockTileResponse(503, b"Service Unavailable")

    mock_client = AsyncMock()
    mock_client.get = mock_get

    img_bytes, min_t, ts, conv = await generate_goes19_telegram_image(client=mock_client)

    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 1000
    loaded_img = Image.open(io.BytesIO(img_bytes))
    assert loaded_img.size == (512, 512)


# ---------------------------------------------------------------------------
# 6. Briefing Diario: Deduplicación Exacta en Base de Datos
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_daily_briefing_deduplication():
    """
    Verifies that the daily briefing fires once when the local hour matches BRIEFING_SEND_HOUR,
    and skips subsequent runs on the same date via alert_key = 'daily_briefing:YYYY-MM-DD'.
    """
    now_cordoba = datetime.now(CORDOBA_TZ)
    current_hour = now_cordoba.hour
    today_str = now_cordoba.strftime("%Y-%m-%d")
    briefing_key = f"daily_briefing:{today_str}"

    # Configure briefing to fire at current hour
    update_alert_settings({
        "briefing_enabled": True,
        "briefing_send_hour": current_hour,
        "briefing_window_start": 22,
        "briefing_window_end": 6,
    })

    with patch("backend.app.services.alerts_service.send_telegram_alert", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"sent": True, "message_id": 9999}

        # First execution: should fire briefing
        res1 = await check_and_dispatch_cron(city_key="rio_cuarto")
        assert res1.get("briefing") is not None
        assert res1["briefing"]["alert_key"] == briefing_key
        assert res1["briefing"]["sent"] is True
        mock_send.assert_called_once()

        # Check DB state
        state = get_alert_state(city="rio_cuarto", alert_key=briefing_key)
        assert state is not None
        assert state["last_sent_utc"] is not None

        # Reset mock
        mock_send.reset_mock()

        # Second execution on same day: must be omitted (deduplicated)
        res2 = await check_and_dispatch_cron(city_key="rio_cuarto")
        assert res2.get("briefing") is None
        mock_send.assert_not_called()


# ---------------------------------------------------------------------------
# 7. Endpoint de Prueba Manual del Briefing (X-ADMIN-PIN)
# ---------------------------------------------------------------------------
def test_briefing_test_endpoint_requires_admin_pin():
    # Without PIN -> 401
    resp1 = client.post("/api/alerts/briefing/test")
    assert resp1.status_code == 401

    # With wrong PIN -> 401
    resp2 = client.post("/api/alerts/briefing/test", headers={"X-ADMIN-PIN": "0000"})
    assert resp2.status_code == 401

    # With valid PIN -> 200
    with patch("backend.app.main.send_telegram_alert", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = {"sent": True, "message_id": 8888}
        resp3 = client.post("/api/alerts/briefing/test", headers={"X-ADMIN-PIN": TEST_PIN})
        assert resp3.status_code == 200
        data = resp3.json()
        assert data["status"] == "ok"
        assert data["result"]["sent"] is True
