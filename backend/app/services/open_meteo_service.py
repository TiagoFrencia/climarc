from typing import Dict, Any, List, Optional
import httpx
from ..config import settings

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
MODELS = ["ecmwf_ifs025", "icon_seamless", "gfs_seamless", "ecmwf_aifs025"]


async def fetch_open_meteo_forecast(
    lat: float,
    lon: float,
    client: Optional[httpx.AsyncClient] = None
) -> Dict[str, Any]:
    """
    Fetches 7-day hourly and daily forecast for ECMWF IFS 0.25, ICON Seamless, GFS Seamless,
    and ECMWF AIFS 0.25 AI model.
    Includes hail, convective shear (500hPa), freezing level height, UV index, and solar times.
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": (
            "temperature_2m,apparent_temperature,relative_humidity_2m,"
            "precipitation,precipitation_probability,wind_speed_10m,"
            "wind_gusts_10m,wind_direction_10m,surface_pressure,pressure_msl,weather_code,cape,"
            "wind_speed_500hPa,wind_direction_500hPa,freezing_level_height,uv_index"
        ),
        "daily": (
            "weather_code,temperature_2m_max,temperature_2m_min,"
            "precipitation_sum,precipitation_probability_max,wind_speed_10m_max,"
            "sunrise,sunset,uv_index_max"
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
                "hourly": [],
                "daily": [],
            }

        data = resp.json()
        raw_hourly = data.get("hourly", {})
        raw_daily = data.get("daily", {})

        times = raw_hourly.get("time", [])

        # Structure hourly series by timestamp
        hourly_records: List[Dict[str, Any]] = []
        for idx, t_raw in enumerate(times):
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
                w_500 = _get_series_val(raw_hourly, f"wind_speed_500hPa_{m}", idx)
                dir_500 = _get_series_val(raw_hourly, f"wind_direction_500hPa_{m}", idx)
                frz_lvl = _get_series_val(raw_hourly, f"freezing_level_height_{m}", idx)
                uv = _get_series_val(raw_hourly, f"uv_index_{m}", idx)

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
                    "wind_speed_500hPa_kmh": w_500,
                    "wind_direction_500hPa_deg": dir_500,
                    "freezing_level_height_m": frz_lvl,
                    "uv_index": uv,
                }

            # Best available fallback across models for convective / atmospheric parameters
            best_w500 = _first_valid([model_data[m].get("wind_speed_500hPa_kmh") for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]])
            best_d500 = _first_valid([model_data[m].get("wind_direction_500hPa_deg") for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]])
            best_frz = _first_valid([model_data[m].get("freezing_level_height_m") for m in ["icon_seamless", "gfs_seamless", "ecmwf_ifs025"]])
            best_uv = _first_valid([model_data[m].get("uv_index") for m in ["gfs_seamless", "icon_seamless", "ecmwf_ifs025"]]) or 0.0

            hourly_records.append({
                "timestamp_utc": t_iso,
                "models": model_data,
                "wind_speed_500hPa_kmh": best_w500,
                "wind_direction_500hPa_deg": best_d500,
                "freezing_level_height_m": best_frz,
                "uv_index": round(best_uv, 1) if best_uv is not None else 0.0,
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
                    "sunrise": _get_series_str(raw_daily, f"sunrise_{m}", idx),
                    "sunset": _get_series_str(raw_daily, f"sunset_{m}", idx),
                    "uv_index_max": _get_series_val(raw_daily, f"uv_index_max_{m}", idx),
                }

            best_sunrise = _first_valid_str([daily_model_data[m].get("sunrise") for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]])
            best_sunset = _first_valid_str([daily_model_data[m].get("sunset") for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]])
            best_uv_max = _first_valid([daily_model_data[m].get("uv_index_max") for m in ["gfs_seamless", "icon_seamless", "ecmwf_ifs025"]])

            daily_records.append({
                "date": date_str,
                "models": daily_model_data,
                "sunrise": best_sunrise,
                "sunset": best_sunset,
                "uv_index_max": best_uv_max,
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
        return float(val) if val is not None else None
    return None


def _get_series_str(data_dict: Dict[str, Any], key: str, index: int) -> Optional[str]:
    series = data_dict.get(key)
    if series and index < len(series):
        val = series[index]
        return str(val) if val is not None else None
    return None


def _first_valid(items: List[Optional[float]]) -> Optional[float]:
    for item in items:
        if item is not None:
            return item
    return None


def _first_valid_str(items: List[Optional[str]]) -> Optional[str]:
    for item in items:
        if item and item.strip():
            return item.strip()
    return None
