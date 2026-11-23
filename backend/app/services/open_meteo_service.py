from typing import Dict, Any, List, Optional
import httpx
from ..config import settings

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]


async def fetch_open_meteo_forecast(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Fetches 7-day hourly and daily forecast for ECMWF IFS 0.25, ICON Seamless, and GFS Seamless.
    Returns parsed dictionary structured by model and hourly/daily timeseries.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,precipitation_probability,wind_speed_10m,"
            "wind_gusts_10m,wind_direction_10m,surface_pressure,pressure_msl,weather_code,cape"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_probability_max,wind_speed_10m_max"
        ),
        "timezone": "UTC",
        "models": ",".join(MODELS),
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        resp = await client.get(OPEN_METEO_URL, params=params)
        if resp.status_code != 200:
            return {
                "available": False,
                "error": f"Open-Meteo returned status {resp.status_code}",
                "hourly": {},
                "daily": {},
            }

        data = resp.json()
        raw_hourly = data.get("hourly", {})
        raw_daily = data.get("daily", {})

        times = raw_hourly.get("time", [])

        # Structure hourly series by timestamp
        # Formatted timestamps: e.g. "2026-11-23T00:00:00Z"
        hourly_records: List[Dict[str, Any]] = []
        for idx, t_raw in enumerate(times):
            # Normalize ISO timestamp to UTC with Z suffix
            t_iso = f"{t_raw}:00Z" if len(t_raw) == 16 else f"{t_raw}Z"

            model_data = {}
            for m in MODELS:
                t2 = _get_series_val(raw_hourly, f"temperature_2m_{m}", idx)
                app_t2 = _get_series_val(raw_hourly, f"apparent_temperature_{m}", idx)
                rh2 = _get_series_val(raw_hourly, f"relative_humidity_2m_{m}", idx)
                precip = _get_series_val(raw_hourly, f"precipitation_{m}", idx)
                precip_prob = _get_series_val(raw_hourly, f"precipitation_probability_{m}", idx)
                wspd = _get_series_val(raw_hourly, f"wind_speed_10m_{m}", idx)
                wgst = _get_series_val(raw_hourly, f"wind_gusts_10m_{m}", idx)
                wdir = _get_series_val(raw_hourly, f"wind_direction_10m_{m}", idx)
                psfc = _get_series_val(raw_hourly, f"surface_pressure_{m}", idx)
                pmsl = _get_series_val(raw_hourly, f"pressure_msl_{m}", idx)
                wcode = _get_series_val(raw_hourly, f"weather_code_{m}", idx)
                cape = _get_series_val(raw_hourly, f"cape_{m}", idx)

                model_data[m] = {
                    "temperature_c": t2,
                    "apparent_temperature_c": app_t2,
                    "relative_humidity_pct": rh2,
                    "precip_mm": precip,
                    "precip_prob_pct": precip_prob,
                    "wind_speed_kmh": wspd,
                    "wind_gust_kmh": wgst,
                    "wind_direction_deg": wdir,
                    "surface_pressure_hpa": psfc,
                    "pressure_msl_hpa": pmsl,
                    "weather_code": wcode,
                    "cape_jkg": cape,
                }

            hourly_records.append({
                "timestamp_utc": t_iso,
                "models": model_data,
            })

        # Structure daily series
        daily_times = raw_daily.get("time", [])
        daily_records: List[Dict[str, Any]] = []
        for idx, date_str in enumerate(daily_times):
            daily_model_data = {}
            for m in MODELS:
                daily_model_data[m] = {
                    "temperature_max_c": _get_series_val(raw_daily, f"temperature_2m_max_{m}", idx),
                    "temperature_min_c": _get_series_val(raw_daily, f"temperature_2m_min_{m}", idx),
                    "precipitation_sum_mm": _get_series_val(raw_daily, f"precipitation_sum_{m}", idx),
                    "precipitation_probability_max_pct": _get_series_val(raw_daily, f"precipitation_probability_max_{m}", idx),
                    "wind_speed_max_kmh": _get_series_val(raw_daily, f"wind_speed_10m_max_{m}", idx),
                    "weather_code": _get_series_val(raw_daily, f"weather_code_{m}", idx),
                }

            daily_records.append({
                "date": date_str,
                "models": daily_model_data,
            })

        return {
            "available": True,
            "latitude": lat,
            "longitude": lon,
            "hourly": hourly_records,
            "daily": daily_records,
        }

    except Exception as exc:
        return {
            "available": False,
            "error": str(exc),
            "hourly": [],
            "daily": [],
        }
    finally:
        if close_client:
            await client.aclose()


def _get_series_val(data_dict: Dict[str, Any], key: str, index: int) -> Optional[float]:
    series = data_dict.get(key)
    if series and index < len(series):
        val = series[index]
        return val if val is not None else None
    return None
