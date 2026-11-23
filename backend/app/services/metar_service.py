import re
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
import httpx

from ..config import settings
from ..database import save_metar_record, get_metar_history

METAR_URL = "https://aviationweather.gov/api/data/metar"
WEATHER_CODES_REGEX = re.compile(
    r"\b(\+|-|VC)?(?:(TS|SH|FZ|BL|DR)(RA|DZ|SN|SG|IC|PL|GR|GS|UP)?|(RA|DZ|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS))\b"
)


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
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Dict[str, Any]]:
    """
    Fetches live METAR reports from aviationweather.gov for requested stations.
    Parses and persists readings into SQLite metar_history.
    Falls back gracefully to SQLite cache on network issues.
    """
    stations = station_ids or ["SAOC", "SACO"]
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

                    # Persist into database for history and pressure trend calculation
                    if obs_time_utc:
                        save_metar_record(
                            icao=icao,
                            obs_time_utc=obs_time_utc,
                            temp_c=temp_c,
                            wind_kmh=wind_kmh,
                            qnh_hpa=altim_hpa,
                            raw_metar=raw_ob,
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
                    "source": "unavailable",
                }

    return results
