import re
import math
import time
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
import httpx

from ..config import settings
from ..database import save_metar_record, get_metar_history

METAR_URL = "https://aviationweather.gov/api/data/metar"
WEATHER_CODES_REGEX = re.compile(
    r"\b(\+|-|VC)?(?:(TS|SH|FZ|BL|DR)(RA|DZ|SN|SG|IC|PL|GR|GS|UP)?|(RA|DZ|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS))\b"
)

# In-memory cache for live METAR observations (5 min TTL to capture SPECI and hourly reports promptly)
_METAR_CACHE: Dict[str, Dict[str, Any]] = {}
_METAR_CACHE_TIME: float = 0.0
METAR_CACHE_TTL_SECONDS: float = 300.0  # 5 minutes


def parse_sky_condition(raw_metar: Optional[str], clouds_data: Optional[Any] = None) -> str:
    """
    Parses cloud coverage from raw METAR or JSON clouds data.
    Hierarchy: overcast > broken > scattered > few > clear > unknown.
    Returns: 'clear', 'few', 'scattered', 'broken', 'overcast', 'unknown'
    """
    raw = (raw_metar or "").upper()

    covers = set()
    if isinstance(clouds_data, list):
        for c in clouds_data:
            if isinstance(c, dict):
                cov = str(c.get("cover", "")).upper()
                if cov:
                    covers.add(cov)

    # 1. Overcast (8/8)
    if re.search(r"\bOVC(?:\d{3}|/{3})?(?:CB|TCU)?\b", raw) or "OVC" in covers:
        return "overcast"
    # 2. Broken (5-7/8)
    if re.search(r"\bBKN(?:\d{3}|/{3})?(?:CB|TCU)?\b", raw) or "BKN" in covers:
        return "broken"
    # 3. Scattered (3-4/8)
    if re.search(r"\bSCT(?:\d{3}|/{3})?(?:CB|TCU)?\b", raw) or "SCT" in covers:
        return "scattered"
    # 4. Few (1-2/8)
    if re.search(r"\bFEW(?:\d{3}|/{3})?(?:CB|TCU)?\b", raw) or "FEW" in covers:
        return "few"
    # 5. Clear (0/8)
    if re.search(r"\b(CAVOK|SKC|CLR|NSC|NCD)\b", raw) or any(c in covers for c in ("CAVOK", "SKC", "CLR", "NSC", "NCD", "CLEAR")):
        return "clear"

    return "unknown"


def calculate_relative_humidity(temp_c: Optional[float], dewp_c: Optional[float]) -> Optional[float]:
    """
    Computes Relative Humidity (%) from Temperature and Dewpoint using the Magnus-Tetens formula:
    RH = 100 * exp((17.625 * Td)/(243.04 + Td) - (17.625 * T)/(243.04 + T))
    """
    if temp_c is None or dewp_c is None:
        return None
    try:
        a = 17.625
        b = 243.04
        alpha = (a * dewp_c) / (b + dewp_c)
        beta = (a * temp_c) / (b + temp_c)
        rh = 100.0 * math.exp(alpha - beta)
        return round(max(0.0, min(100.0, rh)), 1)
    except Exception:
        return None


def extract_wx_codes(raw_metar: str, wx_string: Optional[str] = None) -> List[str]:
    """Extracts significant weather phenomenon codes (e.g. TS, TSRA, GR, SQ, +RA)."""
    codes = set()
    if wx_string:
        for part in wx_string.split():
            clean = part.strip()
            if clean:
                codes.add(clean)

    if raw_metar:
        matches = WEATHER_CODES_REGEX.findall(raw_metar)
        for m in matches:
            code = "".join(m).strip()
            if code:
                codes.add(code)

    return sorted(list(codes))


async def fetch_metar_observations(
    station_ids: Optional[List[str]] = None,
    client: Optional[httpx.AsyncClient] = None,
    force_refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """
    Fetches live METAR reports from aviationweather.gov for requested stations.
    Parses and persists readings into SQLite metar_history.
    Caches results in-memory with a 5-minute TTL to capture SPECI and hourly reports promptly.
    Falls back gracefully to SQLite cache on network issues.
    """
    global _METAR_CACHE, _METAR_CACHE_TIME

    stations = station_ids or ["SAOC", "SACO", "SAOR", "SAOL"]
    now_ts = time.time()

    # Serve from in-memory cache if fresh and covers requested stations
    if not force_refresh and _METAR_CACHE and (now_ts - _METAR_CACHE_TIME) < METAR_CACHE_TTL_SECONDS:
        if all(s in _METAR_CACHE for s in stations):
            return {s: _METAR_CACHE[s] for s in stations}

    ids_param = ",".join(stations)
    url = f"{METAR_URL}?ids={ids_param}&format=json"
    headers = {"User-Agent": settings.USER_AGENT}

    results: Dict[str, Dict[str, Any]] = {}
    now = datetime.now(timezone.utc)

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(url, headers=headers)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                for item in data:
                    icao = item.get("icaoId")
                    if not icao:
                        continue

                    temp_c = item.get("temp")
                    dewp_c = item.get("dewp")
                    wspd_kt = item.get("wspd")
                    wgst_kt = item.get("wgst")
                    wdir_deg = item.get("wdir")
                    altim_hpa = item.get("altim")
                    raw_ob = item.get("rawOb", "")
                    wx_string = item.get("wxString")

                    # Conversions
                    wind_kmh = round(wspd_kt * 1.852, 1) if wspd_kt is not None else None
                    wind_gust_kmh = round(wgst_kt * 1.852, 1) if wgst_kt is not None else None
                    humidity_pct = calculate_relative_humidity(temp_c, dewp_c)

                    # Timestamp parsing
                    obs_time_raw = item.get("reportTime") or item.get("receiptTime")
                    obs_time_utc = None
                    age_minutes = None

                    if obs_time_raw:
                        clean_time = obs_time_raw.replace("Z", "+00:00")
                        try:
                            obs_dt = datetime.fromisoformat(clean_time)
                            obs_time_utc = obs_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                            age_minutes = round((now - obs_dt).total_seconds() / 60.0, 1)
                        except Exception:
                            obs_time_utc = obs_time_raw

                    if obs_time_utc is None and item.get("obsTime"):
                        obs_dt = datetime.fromtimestamp(item["obsTime"], timezone.utc)
                        obs_time_utc = obs_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
                        age_minutes = round((now - obs_dt).total_seconds() / 60.0, 1)

                    wx_codes = extract_wx_codes(raw_ob, wx_string)
                    sky_condition = parse_sky_condition(raw_ob, item.get("clouds"))

                    # Persist into database for history and pressure trend calculation
                    if obs_time_utc:
                        save_metar_record(
                            icao=icao,
                            obs_time_utc=obs_time_utc,
                            temp_c=temp_c,
                            wind_kmh=wind_kmh,
                            qnh_hpa=altim_hpa,
                            raw_metar=raw_ob,
                            wind_dir_deg=wdir_deg,
                            dewp_c=dewp_c,
                        )

                    results[icao] = {
                        "icao": icao,
                        "obs_time_utc": obs_time_utc,
                        "age_minutes": age_minutes,
                        "temp_c": temp_c,
                        "dewp_c": dewp_c,
                        "humidity_pct": humidity_pct,
                        "wind_speed_kmh": wind_kmh,
                        "wind_gust_kmh": wind_gust_kmh,
                        "wind_direction_deg": wdir_deg,
                        "qnh_hpa": altim_hpa,
                        "raw_metar": raw_ob,
                        "wx_string": wx_string,
                        "wx_codes": wx_codes,
                        "sky_condition": sky_condition,
                        "source": "aviationweather.gov",
                    }
    except Exception as exc:
        # Fallback to local DB history
        pass
    finally:
        if close_client:
            await client.aclose()

    # Fill any missing requested stations from database history
    for icao in stations:
        if icao not in results:
            history = get_metar_history(icao, hours=24)
            if history:
                latest = history[0]
                obs_dt = None
                age_minutes = None
                try:
                    t_clean = latest["obs_time_utc"].replace("Z", "+00:00")
                    obs_dt = datetime.fromisoformat(t_clean)
                    age_minutes = round((now - obs_dt).total_seconds() / 60.0, 1)
                except Exception:
                    pass

                raw = latest.get("raw_metar") or ""
                results[icao] = {
                    "icao": icao,
                    "obs_time_utc": latest.get("obs_time_utc"),
                    "age_minutes": age_minutes,
                    "temp_c": latest.get("temp_c"),
                    "dewp_c": None,
                    "humidity_pct": None,
                    "wind_speed_kmh": latest.get("wind_kmh"),
                    "wind_gust_kmh": None,
                    "wind_direction_deg": None,
                    "qnh_hpa": latest.get("qnh_hpa"),
                    "raw_metar": raw,
                    "wx_string": None,
                    "wx_codes": extract_wx_codes(raw),
                    "sky_condition": parse_sky_condition(raw),
                    "source": "metar_history_cache",
                }
            else:
                results[icao] = {
                    "icao": icao,
                    "obs_time_utc": None,
                    "age_minutes": None,
                    "temp_c": None,
                    "dewp_c": None,
                    "humidity_pct": None,
                    "wind_speed_kmh": None,
                    "wind_gust_kmh": None,
                    "wind_direction_deg": None,
                    "qnh_hpa": None,
                    "raw_metar": "",
                    "wx_string": None,
                    "wx_codes": [],
                    "sky_condition": "unknown",
                    "source": "unavailable",
                }

    # Store fresh entries in in-memory cache
    if results:
        for k, v in results.items():
            _METAR_CACHE[k] = v
        _METAR_CACHE_TIME = now_ts

    return results

    return results


def calculate_qnh_trend(
    icao: str = "SAOC",
    current_qnh: Optional[float] = None,
    history_records: Optional[List[Dict[str, Any]]] = None,
    custom_path: Optional[str] = None,
) -> Tuple[str, Optional[float]]:
    """
    Computes barometric QNH pressure tendency over the last 3 hours:
    - 'subiendo': delta >= +1.5 hPa
    - 'bajando_rapido': delta <= -3.0 hPa
    - 'bajando': delta <= -1.5 hPa
    - 'estable': between -1.5 and +1.5 hPa
    Returns (trend_str, delta_3h_hpa).
    """
    history = history_records
    if history is None:
        try:
            history = get_metar_history(icao=icao, hours=4, custom_path=custom_path)
        except Exception:
            history = []

    if not history:
        return "estable", 0.0 if current_qnh is not None else None

    # Latest QNH
    latest_qnh = current_qnh
    latest_dt = None
    if latest_qnh is None:
        for r in history:
            if r.get("qnh_hpa") is not None:
                latest_qnh = float(r["qnh_hpa"])
                t_str = r.get("obs_time_utc", "")
                try:
                    latest_dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                except Exception:
                    pass
                break

    if latest_qnh is None:
        return "estable", None

    if latest_dt is None:
        latest_dt = datetime.now(timezone.utc)

    # Find the record closest to 3 hours prior (between 1.5h and 4.0h)
    best_record = None
    best_diff_from_3h = float("inf")

    for r in history:
        q = r.get("qnh_hpa")
        t_str = r.get("obs_time_utc")
        if q is not None and t_str:
            try:
                dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
                elapsed_hours = (latest_dt - dt).total_seconds() / 3600.0
                if 1.5 <= elapsed_hours <= 4.0:
                    diff = abs(elapsed_hours - 3.0)
                    if diff < best_diff_from_3h:
                        best_diff_from_3h = diff
                        best_record = r
            except Exception:
                pass

    if not best_record or best_record.get("qnh_hpa") is None:
        return "estable", 0.0

    past_qnh = float(best_record["qnh_hpa"])
    delta = round(latest_qnh - past_qnh, 1)

    if delta <= -3.0:
        trend = "bajando_rapido"
    elif delta <= -1.5:
        trend = "bajando"
    elif delta >= 1.5:
        trend = "subiendo"
    else:
        trend = "estable"

    return trend, delta
