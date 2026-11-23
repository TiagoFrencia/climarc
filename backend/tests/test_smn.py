import asyncio
import pytest
from backend.app.config import settings
from backend.app.services.smn_alerts_service import fetch_smn_alerts


def test_smn_graceful_degradation_without_token(monkeypatch):
    monkeypatch.setattr(settings, "SMN_JWT_TOKEN", "")
    res = asyncio.run(fetch_smn_alerts())

    assert res["smn_available"] is False
    assert res["alerts"] == []
    assert "no configurado" in res["reason"].lower()


def test_smn_graceful_degradation_on_api_error(monkeypatch):
    monkeypatch.setattr(settings, "SMN_JWT_TOKEN", "invalid-mock-token")

    import httpx

    class DummyTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(401, json={"error": "Unauthorized"})

    async def _run():
        async with httpx.AsyncClient(transport=DummyTransport()) as client:
            return await fetch_smn_alerts(client=client)

    res = asyncio.run(_run())

    assert res["smn_available"] is False
    assert res["alerts"] == []
    assert res["status_code"] == 401
