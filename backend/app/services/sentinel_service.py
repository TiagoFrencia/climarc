"""
ClimaRC v2.0 - Sentinel Stations Service
Monitors peripheral aerodromes around Río Cuarto:
- SAOC: Central Station (Río Cuarto Airport)
- SACO: Córdoba Capital (~195 km N/NE)
- SAOR: Villa Reynolds, San Luis (~115 km W/SW) - Primary sentinel for dominant SW convective flow
- SAOL: Laboulaye, Córdoba (~150 km SE) - Secondary sentinel for SE / coastal / frontal pushes

Detects approaching fronts (squalls, hail, heavy storms, severe gusts, sharp thermal drops)
moving towards Río Cuarto before impact occurs at SAOC.
"""

import math
import re
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from ..database import get_metar_history

# Coordinates of Reference (Río Cuarto City Center / Airport)
RIO_CUARTO_LAT = -33.1232
RIO_CUARTO_LON = -64.3493

SENTINEL_CONFIGS: Dict[str, Dict[str, Any]] = {
    "SAOC": {
        "name": "Río Cuarto",
        "description": "Estación Central (Aeropuerto Las Higueras)",
        "lat": -33.085,
        "lon": -64.261,
        "is_central": True,
        "cardinal": "LOCAL",
    },
    "SACO": {
        "name": "Córdoba Capital",
        "description": "Aeropuerto Ing. Ambrosio Taravella",
        "lat": -31.323,
        "lon": -64.208,
        "is_central": False,
        "cardinal": "N",
    },
    "SAOR": {
        "name": "Villa Reynolds",
        "description": "V Brigada Aérea (San Luis) - Centinela SO",
        "lat": -33.729,
        "lon": -65.381,
        "is_central": False,
        "cardinal": "SO",
    },
    "SAOL": {
        "name": "Laboulaye",
        "description": "Aeródromo Laboulaye - Centinela SE",
        "lat": -34.137,
        "lon": -63.361,
        "is_central": False,
        "cardinal": "SE",
    },
}


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance in kilometers."""
    r = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2.0) ** 2
    return round(2.0 * r * math.atan2(math.sqrt(a), math.sqrt(1.0 - a)), 1)


def calculate_bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> int:
    """Calculates forward initial azimuth (bearing) from point 1 to point 2 in degrees (0-359)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dlam = math.radians(lon2 - lon1)
    y = math.sin(dlam) * math.cos(phi2)
    x = math.cos(phi1) * math.sin(phi2) - math.sin(phi1) * math.cos(phi2) * math.cos(dlam)
    deg = math.degrees(math.atan2(y, x))
    return int((deg + 360.0) % 360.0)


def get_sentinels_summary(metar_map: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Generates a structured summary for all 4 sentinel stations.
    Exposes coordinates, distance, bearing, operational status and weather phenomena.
    """
    sentinels = []

    for icao, cfg in SENTINEL_CONFIGS.items():
        obs = metar_map.get(icao, {})
        is_central = cfg.get("is_central", False)

        dist_km = 0.0 if is_central else haversine_distance_km(
            RIO_CUARTO_LAT, RIO_CUARTO_LON, cfg["lat"], cfg["lon"]
        )
        bearing = 0 if is_central else calculate_bearing_deg(
            RIO_CUARTO_LAT, RIO_CUARTO_LON, cfg["lat"], cfg["lon"]
        )

        raw_metar = obs.get("raw_metar", "")
        wx_codes = obs.get("wx_codes", [])
        temp_c = obs.get("temp_c")
        dewp_c = obs.get("dewp_c")
        humidity_pct = obs.get("humidity_pct")
        wind_speed_kmh = obs.get("wind_speed_kmh")
        wind_gust_kmh = obs.get("wind_gust_kmh")
        wind_direction_deg = obs.get("wind_direction_deg")
        qnh_hpa = obs.get("qnh_hpa")
        obs_time_utc = obs.get("obs_time_utc")
        age_minutes = obs.get("age_minutes")
        source = obs.get("source", "unavailable")

        # Operational status: station is operational if we have a fresh reading (< 180 min)
        operational = (
            temp_c is not None
            and age_minutes is not None
            and age_minutes <= 180.0
            and source != "unavailable"
        )

        sentinels.append({
            "icao": icao,
            "name": cfg["name"],
            "description": cfg["description"],
            "lat": cfg["lat"],
            "lon": cfg["lon"],
            "is_central": is_central,
            "distance_km": round(dist_km, 0),
            "bearing_deg": bearing,
            "cardinal": cfg["cardinal"],
            "operational": operational,
            "temp_c": temp_c,
            "dewp_c": dewp_c,
            "humidity_pct": humidity_pct,
            "wind_speed_kmh": wind_speed_kmh,
            "wind_gust_kmh": wind_gust_kmh,
            "wind_direction_deg": wind_direction_deg,
            "qnh_hpa": qnh_hpa,
            "wx_codes": wx_codes,
            "raw_metar": raw_metar,
            "obs_time_utc": obs_time_utc,
            "age_minutes": age_minutes,
        })

    return sentinels


def detect_approaching_front(
    metar_map: Dict[str, Dict[str, Any]],
    historical_metar: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    custom_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Analyzes peripheral sentinel stations (SAOR Villa Reynolds ~115 km W/SW, SAOL Laboulaye ~150 km SE)
    to detect severe fronts, squalls, or hail moving towards Río Cuarto (SAOC).

    Trigger conditions on SAOR (priority 1) or SAOL:
      1. Hail reported: 'GR' or 'GS' in wx_codes or raw METAR.
      2. Squall reported: 'SQ' in wx_codes or raw METAR.
      3. Heavy thunderstorm reported: '+TSRA' or '+TS' in wx_codes or raw METAR.
      4. Severe gusts from S/SW sector: wind_direction in [150°, 270°] AND (wind_gust >= 50 or wind_speed >= 50 km/h).
      5. Sharp thermal drop: current temp dropped >= 6.0 °C compared to 1.0h–2.5h ago.

    Condition for Río Cuarto (SAOC):
      Must NOT have already received the impact (SAOC does not currently report +TSRA, GR, SQ, or S/SW gusts >= 50 km/h).
    """
    saoc_obs = metar_map.get("SAOC", {})
    saoc_raw = (saoc_obs.get("raw_metar") or "").upper()
    saoc_codes = [c.upper() for c in saoc_obs.get("wx_codes", [])]
    saoc_gust = saoc_obs.get("wind_gust_kmh") or 0.0
    saoc_wspd = saoc_obs.get("wind_speed_kmh") or 0.0
    saoc_wdir = saoc_obs.get("wind_direction_deg")

    # Has Río Cuarto already received the severe front?
    saoc_impacted = (
        "GR" in saoc_codes
        or "GS" in saoc_codes
        or "SQ" in saoc_codes
        or "+TSRA" in saoc_codes
        or "+TS" in saoc_codes
        or (" GR " in f" {saoc_raw} ")
        or (" SQ " in f" {saoc_raw} ")
        or ("+TSRA" in saoc_raw)
        or (
            saoc_wdir is not None
            and 150 <= saoc_wdir <= 270
            and max(saoc_gust, saoc_wspd) >= 50.0
        )
    )

    if saoc_impacted:
        return {
            "detected": False,
            "reason": "impact_already_active_in_rio_cuarto",
        }

    # Evaluate sentinels in order of priority: SAOR (Villa Reynolds, dominant SW path), then SAOL (Laboulaye)
    sentinel_candidates = ["SAOR", "SAOL"]

    for icao in sentinel_candidates:
        obs = metar_map.get(icao)
        if not obs or obs.get("temp_c") is None:
            continue

        raw = (obs.get("raw_metar") or "").upper()
        codes = [c.upper() for c in obs.get("wx_codes", [])]
        temp_c = obs.get("temp_c")
        wspd = obs.get("wind_speed_kmh") or 0.0
        wgst = obs.get("wind_gust_kmh") or 0.0
        wdir = obs.get("wind_direction_deg")
        cfg = SENTINEL_CONFIGS.get(icao, {})

        triggers = []

        # 1. Hail
        if "GR" in codes or "GS" in codes or " GR " in f" {raw} " or " GS " in f" {raw} ":
            triggers.append("granizo_reportado")

        # 2. Squall
        if "SQ" in codes or " SQ " in f" {raw} ":
            triggers.append("turbonada_sq")

        # 3. Heavy thunderstorm (+TSRA / +TS)
        if "+TSRA" in codes or "+TS" in codes or "+TSRA" in raw or "+TS " in raw:
            triggers.append("tormenta_fuerte_mas_tsra")

        # 4. Severe gusts from S/SW sector (150° - 270°)
        effective_gust = max(wspd, wgst)
        if wdir is not None and 150 <= wdir <= 270 and effective_gust >= 50.0:
            triggers.append(f"rafagas_sector_sur_so_{round(effective_gust, 0)}kmh")

        # 5. Thermal drop >= 6.0 °C in ~2 hours
        hist = None
        if historical_metar and icao in historical_metar:
            hist = historical_metar[icao]
        else:
            try:
                hist = get_metar_history(icao=icao, hours=4, custom_path=custom_db_path)
            except Exception:
                hist = None

        if hist and temp_c is not None:
            # Look for reading 1.0 to 2.5 hours ago
            now_utc = datetime.now(timezone.utc)
            for record in hist:
                rec_temp = record.get("temp_c")
                t_str = record.get("obs_time_utc")
                if rec_temp is not None and t_str:
                    try:
                        clean_t = t_str.replace("Z", "+00:00")
                        rec_dt = datetime.fromisoformat(clean_t)
                        age_h = (now_utc - rec_dt).total_seconds() / 3600.0
                        if 0.9 <= age_h <= 2.8:
                            drop = rec_temp - temp_c
                            if drop >= 6.0:
                                triggers.append(f"caida_termica_{round(drop, 1)}c_en_{round(age_h, 1)}h")
                                break
                    except Exception:
                        pass

        if triggers:
            dist_km = haversine_distance_km(RIO_CUARTO_LAT, RIO_CUARTO_LON, cfg["lat"], cfg["lon"])
            # Estimate arrival time: speed of front ~ 60 - 80 km/h
            front_speed = max(55.0, min(95.0, effective_gust if effective_gust > 40 else 65.0))
            eta_minutes = int(round((dist_km / front_speed) * 60.0, -1))
            eta_minutes = max(30, min(150, eta_minutes))

            phenomenon_desc = []
            if "granizo_reportado" in triggers:
                phenomenon_desc.append("granizo")
            if "turbonada_sq" in triggers:
                phenomenon_desc.append("turbonada severa")
            if "tormenta_fuerte_mas_tsra" in triggers:
                phenomenon_desc.append("tormenta fuerte (+TSRA)")
            if any("rafagas_sector_sur_so" in t for t in triggers):
                phenomenon_desc.append(f"ráfagas de {round(effective_gust, 0)} km/h del SO/Sur")
            if any("caida_termica" in t for t in triggers):
                phenomenon_desc.append("brusco descenso térmico")

            summary_text = (
                f"Estación {cfg.get('name', icao)} ({icao}, {round(dist_km, 0)} km al {cfg.get('cardinal', 'SO')}) "
                f"registra {', '.join(phenomenon_desc)}. "
                f"Frente en avance hacia Río Cuarto con arribo estimado en {eta_minutes} minutos."
            )

            return {
                "detected": True,
                "station_icao": icao,
                "station_name": cfg.get("name", icao),
                "distance_km": round(dist_km, 0),
                "cardinal": cfg.get("cardinal", "SO"),
                "estimated_arrival_minutes": eta_minutes,
                "triggers": triggers,
                "summary": summary_text,
                "details": {
                    "temp_c": temp_c,
                    "wind_speed_kmh": wspd,
                    "wind_gust_kmh": wgst,
                    "wind_direction_deg": wdir,
                    "raw_metar": raw,
                    "wx_codes": codes,
                },
            }

    return {"detected": False}
