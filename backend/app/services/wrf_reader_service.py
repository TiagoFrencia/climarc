import os
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
from ..config import settings

_WRF_CACHE: Optional[Dict[str, Any]] = None
_WRF_CACHE_EXPIRES_AT: float = 0.0


def _resolve_local_path(target_path: str) -> Optional[Path]:
    p = Path(target_path)
    if p.is_file():
        return p

    # Check relative to project root
    root = Path(__file__).resolve().parents[2]
    cand = root / target_path
    if cand.is_file():
        return cand

    # Check relative to backend dir
    backend_root = Path(__file__).resolve().parents[1]
    cand_b = backend_root / target_path
    if cand_b.is_file():
        return cand_b

    return None


async def read_wrf_forecast(
    force_refresh: bool = False,
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Reads WRF JSON from configured WRF_JSON_URL (HTTP URL or local filesystem).
    Validates cycle freshness (< 18 hours old).
    Caches in memory for fast serving with silent fallback on failure.
    """
    global _WRF_CACHE, _WRF_CACHE_EXPIRES_AT

    now_ts = time.time()
    if not force_refresh and _WRF_CACHE is not None and now_ts < _WRF_CACHE_EXPIRES_AT:
        return _WRF_CACHE

    url = settings.WRF_JSON_URL
    raw_data: Optional[Dict[str, Any]] = None

    close_client = False
    if url.startswith("http://") or url.startswith("https://"):
        if client is None:
            client = httpx.AsyncClient(timeout=10.0)
            close_client = True
        try:
            resp = await client.get(url, headers={"User-Agent": settings.USER_AGENT})
            if resp.status_code == 200:
                raw_data = resp.json()
        except Exception:
            pass
        finally:
            if close_client:
                await client.aclose()
    else:
        local_p = _resolve_local_path(url)
        if local_p and local_p.is_file():
            try:
                with open(local_p, "r", encoding="utf-8") as f:
                    raw_data = json.load(f)
            except Exception:
                pass

    if not raw_data or not isinstance(raw_data, dict):
        result = {
            "wrf_available": False,
            "reason": f"No se pudo cargar el archivo WRF desde '{url}'",
            "cities": {},
            "age_hours": None,
        }
        _WRF_CACHE = result
        _WRF_CACHE_EXPIRES_AT = now_ts + 60.0  # Retry after 1 min on failure
        return result

    metadata = raw_data.get("metadata", {})
    run_time_str = metadata.get("run_time_utc")
    if not run_time_str:
        result = {
            "wrf_available": False,
            "reason": "El archivo WRF carece del campo 'run_time_utc' en metadata",
            "cities": {},
            "age_hours": None,
        }
        _WRF_CACHE = result
        _WRF_CACHE_EXPIRES_AT = now_ts + 60.0
        return result

    # Validate freshness (< 26 hours to comfortably span 12Z / 00Z publishing delays from SMN)
    try:
        clean_run = run_time_str.replace("Z", "+00:00")
        run_dt = datetime.fromisoformat(clean_run)
        now_dt = datetime.now(timezone.utc)
        age_hours = (now_dt - run_dt).total_seconds() / 3600.0

        if age_hours > 26.0:
            result = {
                "wrf_available": False,
                "reason": f"Corrida WRF vencida ({age_hours:.1f} horas de antigüedad, límite 26h)",
                "run_time_utc": run_time_str,
                "age_hours": round(age_hours, 1),
                "cities": {},
            }
            _WRF_CACHE = result
            _WRF_CACHE_EXPIRES_AT = now_ts + 300.0
            return result

        result = {
            "wrf_available": True,
            "reason": "OK",
            "run_time_utc": run_time_str,
            "generated_at_utc": metadata.get("generated_at_utc"),
            "hours_count": metadata.get("hours_count", 0),
            "age_hours": round(age_hours, 1),
            "cities": raw_data.get("cities", {}),
        }
        _WRF_CACHE = result
        _WRF_CACHE_EXPIRES_AT = now_ts + 300.0  # 5 min in-memory cache
        return result

    except Exception as exc:
        result = {
            "wrf_available": False,
            "reason": f"Error validando metadata WRF: {exc}",
            "cities": {},
            "age_hours": None,
        }
        _WRF_CACHE = result
        _WRF_CACHE_EXPIRES_AT = now_ts + 60.0
        return result
