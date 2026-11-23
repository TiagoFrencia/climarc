from typing import Dict, Any, List, Optional
import httpx
from ..config import settings

SMN_WARNING_URL = "https://ws1.smn.gob.ar/v1/warning/shortterm"


async def fetch_smn_alerts(
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Fetches short-term warnings from official SMN API (ws1.smn.gob.ar) with strict graceful degradation.
    If SMN_JWT_TOKEN is empty or not configured, returns immediately without network calls.
    If token is invalid or returns 401/403, falls back silently.
    """
    token = settings.SMN_JWT_TOKEN
    if not token or not token.strip():
        return {
            "smn_available": False,
            "reason": "Token JWT de SMN no configurado (degradación elegante activa)",
            "alerts": [],
        }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
        close_client = True

    headers = {
        "Authorization": f"JWT {token.strip()}",
        "User-Agent": settings.USER_AGENT,
    }

    try:
        resp = await client.get(SMN_WARNING_URL, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            alerts = []
            if isinstance(data, list):
                for item in data:
                    alerts.append({
                        "id": item.get("id"),
                        "title": item.get("title", "Alerta SMN"),
                        "description": item.get("description", ""),
                        "level": item.get("level", "amarillo"),
                        "zones": item.get("zones", []),
                        "source": "SMN-Oficial",
                    })
            return {
                "smn_available": True,
                "alerts": alerts,
            }
        else:
            return {
                "smn_available": False,
                "status_code": resp.status_code,
                "reason": f"SMN API retornó HTTP {resp.status_code}",
                "alerts": [],
            }
    except Exception as exc:
        return {
            "smn_available": False,
            "reason": f"Fallo de conexión con SMN: {exc}",
            "alerts": [],
        }
    finally:
        if close_client:
            await client.aclose()
