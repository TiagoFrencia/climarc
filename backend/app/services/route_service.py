"""
ClimaRC v2.2 - Route Weather & Road Mobility Service
Evaluates road and weather safety for key transport corridors connecting Río Cuarto
with Córdoba Capital, Villa María, Villa Mercedes, Santa Rosa de Calamuchita, and San Luis.

Queries 3 strategic points concurrently (Origin, Geographical Midpoint, Destination) via Open-Meteo:
  - Computes global corridor status: 'optimo' | 'precaucion' | 'peligroso'
  - Aggregates maximum precipitation (mm) and peak crosswind gusts (km/h)
  - Assesses fog/visibility and road surface hazards
  - 15-minute SQLite cache to guarantee instant response and avoid rate limits.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import httpx
from ..config import settings
from ..database import get_cached_payload, set_cached_payload

logger = logging.getLogger(__name__)

OPEN_METEO_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"

# Preconfigured origins & destinations around central Argentina
ROUTE_ORIGIN = {
    "key": "rio_cuarto",
    "name": "Río Cuarto",
    "lat": -33.1307,
    "lon": -64.3499,
}

ROUTE_PRESETS: Dict[str, Dict[str, Any]] = {
    "cordoba": {
        "key": "cordoba",
        "name": "Córdoba Capital",
        "route_name": "RN 36 Autovía Río Cuarto - Córdoba",
        "lat": -31.4201,
        "lon": -64.1888,
        "midpoint_name": "Tramo Medio (Berrotarán / Almafuerte)",
    },
    "villa_maria": {
        "key": "villa_maria",
        "name": "Villa María",
        "route_name": "RN 158 Corredor Río Cuarto - Villa María",
        "lat": -32.4075,
        "lon": -63.2403,
        "midpoint_name": "Tramo Medio (Gral. Cabrera / Carnerillo)",
    },
    "villa_mercedes": {
        "key": "villa_mercedes",
        "name": "Villa Mercedes",
        "route_name": "RN 8 Corredor Río Cuarto - Villa Mercedes",
        "lat": -33.6758,
        "lon": -65.4578,
        "midpoint_name": "Tramo Medio (Sampacho / Chaján)",
    },
    "santa_rosa_calamuchita": {
        "key": "santa_rosa_calamuchita",
        "name": "Santa Rosa de Calamuchita",
        "route_name": "RP 23 / RP 5 Camino de las Sierras",
        "lat": -32.0694,
        "lon": -64.5367,
        "midpoint_name": "Tramo Medio Sierras (Elena / Calamuchita)",
    },
    "san_luis": {
        "key": "san_luis",
        "name": "San Luis",
        "route_name": "RN 8 / RN 7 Río Cuarto - San Luis",
        "lat": -33.2950,
        "lon": -66.3356,
        "midpoint_name": "Tramo Medio (Fraga / Mercedes)",
    },
}

WMO_DESCRIPTIONS: Dict[int, str] = {
    0: "Despejado",
    1: "Mayormente despejado",
    2: "Parcialmente nublado",
    3: "Nublado",
    45: "Niebla",
    48: "Niebla engelante",
    51: "Llovizna leve",
    53: "Llovizna moderada",
    55: "Llovizna densa",
    61: "Lluvia débil",
    63: "Lluvia moderada",
    65: "Lluvia fuerte",
    71: "Nevada débil",
    73: "Nevada moderada",
    75: "Nevada fuerte",
    80: "Chubascos débiles",
    81: "Chubascos moderados",
    82: "Chubascos fuertes",
    95: "Tormenta eléctrica",
    96: "Tormenta con granizo",
    99: "Tormenta severa con granizo",
}


def _get_condition_label(weather_code: int) -> str:
    return WMO_DESCRIPTIONS.get(weather_code, "Parcialmente nublado")


async def _fetch_point_weather(
    lat: float,
    lon: float,
    client: httpx.AsyncClient,
) -> Dict[str, Any]:
    """Fetches point weather from Open-Meteo with 6h horizon."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "current": (
            "temperature_2m,relative_humidity_2m,apparent_temperature,"
            "precipitation,weather_code,wind_speed_10m,wind_gusts_10m,wind_direction_10m"
        ),
        "hourly": "precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m,weather_code,visibility",
        "forecast_hours": 6,
        "timezone": "America/Argentina/Cordoba",
    }
    try:
        resp = await client.get(OPEN_METEO_FORECAST_URL, params=params, headers={"User-Agent": settings.USER_AGENT})
        if resp.status_code == 200:
            return resp.json()
        logger.warning(f"Open-Meteo returned {resp.status_code} for point ({lat}, {lon})")
        return {}
    except Exception as exc:
        logger.warning(f"Error fetching point weather ({lat}, {lon}): {exc}")
        return {}


def _evaluate_segment(
    segment_id: str,
    name: str,
    lat: float,
    lon: float,
    raw_data: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Parses Open-Meteo point response and computes road safety status for the segment.
    """
    curr = raw_data.get("current", {})
    hourly = raw_data.get("hourly", {})

    temp_c = curr.get("temperature_2m")
    if temp_c is None:
        temp_c = 21.0
    app_temp_c = curr.get("apparent_temperature", temp_c)
    wcode = curr.get("weather_code", 0)

    # Rain: max of current and next 3 hours
    curr_rain = curr.get("precipitation", 0.0) or 0.0
    h_rains = [r for r in (hourly.get("precipitation") or [])[:3] if r is not None]
    max_rain = max([curr_rain] + h_rains) if h_rains else curr_rain

    h_probs = [p for p in (hourly.get("precipitation_probability") or [])[:3] if p is not None]
    max_prob = max(h_probs) if h_probs else (80 if curr_rain > 0 else 0)

    # Wind & gusts: max of current and next 3 hours
    w_speed = curr.get("wind_speed_10m", 15.0) or 15.0
    curr_gust = curr.get("wind_gusts_10m", w_speed) or w_speed
    h_gusts = [g for g in (hourly.get("wind_gusts_10m") or [])[:3] if g is not None]
    max_gust = max([curr_gust] + h_gusts) if h_gusts else curr_gust
    w_dir = curr.get("wind_direction_10m", 0.0) or 0.0

    # Visibility (meters to km)
    vis_list = [v for v in (hourly.get("visibility") or [])[:3] if v is not None]
    min_vis_m = min(vis_list) if vis_list else 10000.0
    vis_km = round(min_vis_m / 1000.0, 1)

    # Safety Classification:
    # 'peligroso':
    #   - Rain >= 10.0 mm OR thunderstorm/hail (95, 96, 99)
    #   - Wind gusts >= 65.0 km/h (severe truck/car crosswind)
    #   - Visibility < 1.0 km (dense fog)
    # 'precaucion':
    #   - Rain >= 0.5 mm OR rain weather codes (51..65, 80..82)
    #   - Wind gusts >= 40.0 km/h (noticeable crosswind)
    #   - Visibility < 4.0 km (haze / light fog)
    # 'optimo':
    #   - Clear / dry road, calm wind, visibility >= 4 km
    is_storm = wcode in (95, 96, 99)
    is_rain = max_rain >= 0.5 or wcode in (51, 53, 55, 61, 63, 65, 80, 81, 82)

    if max_rain >= 10.0 or is_storm or max_gust >= 65.0 or vis_km < 1.0:
        status = "peligroso"
        if is_storm:
            summary = f"Tormenta eléctrica en curso. Ráfagas de {round(max_gust)} km/h. Calzada anegada."
        elif max_gust >= 65.0:
            summary = f"Peligro por fuertes ráfagas cruzadas ({round(max_gust)} km/h). Extremar precaución."
        elif vis_km < 1.0:
            summary = f"Niebla densa (visibilidad {vis_km} km). Reducir velocidad inmediatamente."
        else:
            summary = f"Lluvia torrencial ({round(max_rain, 1)} mm). Riesgo de hidroplaneo."
    elif is_rain or max_gust >= 40.0 or vis_km < 4.0:
        status = "precaucion"
        if is_rain and max_gust >= 40.0:
            summary = f"Calzada húmeda por lluvias ({round(max_rain, 1)} mm) y ráfagas de {round(max_gust)} km/h."
        elif is_rain:
            summary = f"Lluvia intermitente ({round(max_rain, 1)} mm). Asfalto mojado, aumentar distancia de frenado."
        elif max_gust >= 40.0:
            summary = f"Ráfagas moderadas a fuertes ({round(max_gust)} km/h). Atención al viento lateral."
        else:
            summary = f"Visibilidad reducida por bruma o niebla ({vis_km} km)."
    else:
        status = "optimo"
        summary = "Calzada seca, visibilidad óptima y viento moderado. Condiciones ideales."

    return {
        "segment_id": segment_id,
        "name": name,
        "lat": round(lat, 4),
        "lon": round(lon, 4),
        "status": status,
        "temperature_c": round(temp_c, 1),
        "apparent_temperature_c": round(app_temp_c, 1),
        "condition": _get_condition_label(wcode),
        "weather_code": wcode,
        "rain_mm": round(max_rain, 1),
        "precip_prob_pct": round(max_prob, 0),
        "wind_speed_kmh": round(w_speed, 1),
        "wind_gust_kmh": round(max_gust, 1),
        "wind_direction_deg": round(w_dir, 0),
        "visibility_km": vis_km,
        "summary": summary,
    }


async def get_route_weather(
    origin: str = "rio_cuarto",
    destination: str = "cordoba",
    dest_lat: Optional[float] = None,
    dest_lon: Optional[float] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Evaluates weather across a 3-point corridor from Río Cuarto to destination.
    Uses 15-minute SQLite cache.
    """
    orig_cfg = ROUTE_ORIGIN
    orig_name = orig_cfg["name"]
    orig_lat = orig_cfg["lat"]
    orig_lon = orig_cfg["lon"]

    # Resolve destination
    dest_preset = ROUTE_PRESETS.get(destination.lower().strip())
    if dest_preset and dest_lat is None and dest_lon is None:
        dest_name = dest_preset["name"]
        route_name = dest_preset["route_name"]
        target_lat = dest_preset["lat"]
        target_lon = dest_preset["lon"]
        midpoint_name = dest_preset["midpoint_name"]
        dest_key = dest_preset["key"]
    elif dest_lat is not None and dest_lon is not None:
        dest_key = "custom"
        dest_name = "Destino Seleccionado"
        route_name = f"Trayecto Río Cuarto a ({dest_lat:.2f}, {dest_lon:.2f})"
        target_lat = float(dest_lat)
        target_lon = float(dest_lon)
        midpoint_name = "Tramo Medio del Trayecto"
    else:
        # Default fallback to Córdoba Capital
        dest_preset = ROUTE_PRESETS["cordoba"]
        dest_name = dest_preset["name"]
        route_name = dest_preset["route_name"]
        target_lat = dest_preset["lat"]
        target_lon = dest_preset["lon"]
        midpoint_name = dest_preset["midpoint_name"]
        dest_key = dest_preset["key"]

    # Midpoint
    mid_lat = round((orig_lat + target_lat) / 2.0, 4)
    mid_lon = round((orig_lon + target_lon) / 2.0, 4)

    cache_key = f"route_weather:{orig_cfg['key']}:{dest_key}:{target_lat:.4f}_{target_lon:.4f}"
    cached = get_cached_payload(cache_key, max_age_seconds=900)
    if cached:
        return cached

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS)
        close_client = True

    try:
        # Fetch 3 points concurrently
        task_orig = _fetch_point_weather(orig_lat, orig_lon, client)
        task_mid = _fetch_point_weather(mid_lat, mid_lon, client)
        task_dest = _fetch_point_weather(target_lat, target_lon, client)

        res_orig, res_mid, res_dest = await asyncio.gather(task_orig, task_mid, task_dest)

        seg_orig = _evaluate_segment("origin", f"{orig_name} (Origen)", orig_lat, orig_lon, res_orig)
        seg_mid = _evaluate_segment("midpoint", midpoint_name, mid_lat, mid_lon, res_mid)
        seg_dest = _evaluate_segment("destination", f"{dest_name} (Destino)", target_lat, target_lon, res_dest)

        segments = [seg_orig, seg_mid, seg_dest]

        # Global corridor status
        statuses = [s["status"] for s in segments]
        if "peligroso" in statuses:
            global_status = "peligroso"
        elif "precaucion" in statuses:
            global_status = "precaucion"
        else:
            global_status = "optimo"

        max_rain = round(max(s["rain_mm"] for s in segments), 1)
        max_gust = round(max(s["wind_gust_kmh"] for s in segments), 1)

        # Global Corridor Executive Summary
        peligrosos = [s["name"].split(" (")[0] for s in segments if s["status"] == "peligroso"]
        precauciones = [s["name"].split(" (")[0] for s in segments if s["status"] == "precaucion"]

        if global_status == "peligroso":
            sec_str = ", ".join(peligrosos)
            global_summary = (
                f"Condiciones peligrosas en {sec_str}: ráfagas de hasta {round(max_gust)} km/h o tormentas. "
                "Evitar traslados si no es indispensable."
            )
        elif global_status == "precaucion":
            sec_str = ", ".join(precauciones)
            global_summary = (
                f"Ruta transitable con precaución en {sec_str}: asfalto mojado o ráfagas cruzadas de "
                f"{round(max_gust)} km/h. Conducir a velocidad moderada."
            )
        else:
            global_summary = (
                f"Corredor {route_name} en condiciones óptimas: calzada seca, visibilidad normal y viento moderado "
                f"({round(max_gust)} km/h)."
            )

        now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        payload = {
            "origin": orig_cfg["key"],
            "origin_name": orig_name,
            "destination": dest_key,
            "destination_name": dest_name,
            "route_name": route_name,
            "global_status": global_status,
            "max_rain_mm": max_rain,
            "max_gust_kmh": max_gust,
            "summary": global_summary,
            "updated_at_utc": now_utc,
            "segments": segments,
        }

        set_cached_payload(cache_key, payload)
        return payload

    finally:
        if close_client:
            await client.aclose()
