"""
ClimaRC v2.0 - Two-Stage Hail Prediction & Warning Engine (Módulo Antigranizo)
Stage 1: Atmospheric Hail Potential (1 to 12 hours)
  - 0-6 km Deep-Layer Vertical Wind Shear (vectorial difference between 10m and 500 hPa winds)
  - Convective Available Potential Energy (CAPE)
  - Freezing Level Height (0°C isotherm, optimal hazard zone in Córdoba: 2,800 - 4,300 m)
  - Convective Trigger / Lift (precipitation and regional WRF 3x3 neighborhood maximums)
  - Produces normalized score (0-100), level (sin_riesgo, moderado, alto, extremo) and peak window.

Stage 2: Live GOES-19 Band 13 Satellite Scanner (15 to 60 minutes)
  - Downloads lightweight calibrated grayscale tile (z=6, x=20, y=38) from SSEC RealEarth (~25-35 KB)
  - Scans ~60 km sector to the West, Southwest, South, and over Río Cuarto
  - Calibrated brightness thresholds derived directly from RealEarth legend:
      * Pixel >= 179: Cloud-top temperature < -60 °C (severe convective storm top)
      * Pixel >= 195: Cloud-top temperature < -70 °C (extreme overshooting convective top)
  - Anti-false-positive filter (MANDATORY):
      * Only triggers imminent hail alert if:
        (a) Surface sentinel station (SAOC or SAOR) reports literal GR/GS, OR
        (b) Stage 2 detects severe cold cores AND Stage 1 potential is >= moderado (rejects harmless cirrus).
"""

import io
import math
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import httpx
import numpy as np
from PIL import Image

from ..config import settings

logger = logging.getLogger(__name__)

CORDOBA_TZ = timezone(timedelta(hours=-3))

# RealEarth Endpoints
REALEARTH_TIMES_URL = "https://realearth.ssec.wisc.edu/api/times?products=G19-ABI-FD-BAND13"
REALEARTH_TILE_TEMPLATE = "https://realearth.ssec.wisc.edu/tiles/G19-ABI-FD-BAND13/6/20/38.png?time={time}"

# Calibrated Thresholds from RealEarth Legend (Gray = 88 - 1.51 * T_c)
PIXEL_THRESHOLD_SEVERE = 179   # -60 °C
PIXEL_THRESHOLD_EXTREME = 195  # -70 °C

# Tile coordinates for Río Cuarto and ~60 km perimeter at z=6 (tile 20, 38)
# Center of Río Cuarto: px_x = 143, px_y = 63
SECTOR_X_MIN = 110
SECTOR_X_MAX = 155
SECTOR_Y_MIN = 55
SECTOR_Y_MAX = 95

# In-memory cache for satellite scan to avoid redundant downloads
_SATELLITE_SCAN_CACHE: Optional[Dict[str, Any]] = None
_SATELLITE_SCAN_CACHE_TIME: float = 0.0
_SATELLITE_SCAN_TTL: float = 600.0  # 10 minutes cache


def calculate_wind_shear_0_6km(
    wspd_10m_kmh: Optional[float],
    wdir_10m_deg: Optional[float],
    wspd_500hpa_kmh: Optional[float],
    wdir_500hpa_deg: Optional[float],
) -> float:
    """
    Computes 0-6 km deep layer vertical wind shear (km/h) as the true vectorial difference
    between 10m surface wind and 500 hPa (~5.5-6 km altitude) mid-tropospheric wind.
    """
    if wspd_10m_kmh is None:
        wspd_10m_kmh = 0.0
    if wspd_500hpa_kmh is None:
        return round(float(wspd_10m_kmh), 1)

    dir_10 = wdir_10m_deg if wdir_10m_deg is not None else 0.0
    dir_500 = wdir_500hpa_deg if wdir_500hpa_deg is not None else dir_10

    # Meteorological wind: direction FROM which wind blows
    rad_10 = math.radians(dir_10)
    u_10 = -wspd_10m_kmh * math.sin(rad_10)
    v_10 = -wspd_10m_kmh * math.cos(rad_10)

    rad_500 = math.radians(dir_500)
    u_500 = -wspd_500hpa_kmh * math.sin(rad_500)
    v_500 = -wspd_500hpa_kmh * math.cos(rad_500)

    du = u_500 - u_10
    dv = v_500 - v_10

    shear_mag = math.sqrt(du * du + dv * dv)
    return round(shear_mag, 1)


def compute_stage1_step_score(
    cape: float,
    shear_kmh: float,
    freezing_level_m: Optional[float],
    precip_prob: float,
    precip_mm: float,
    wrf_nearby_mm: float,
) -> float:
    """
    Calculates atmospheric hail score (0 to 100) for a single forecast step.
    Weighted meteorological components modulated by convective CAPE gate (similar to SHIP):
      - CAPE (0-35 points)
      - Wind Shear 0-6km (0-30 points)
      - Freezing Level Altitude (0-15 points, optimal 2,800 - 4,300 m)
      - Convective Trigger / Lift (0-20 points)
    Strict convective energy gates:
      - cape < 500 J/kg  -> score <= 20 (sin_riesgo)
      - 500 <= cape < 1100 J/kg -> score <= 45 (moderado, never alto)
      - alto (>= 50) requires cape >= 1100 J/kg and shear >= 40 km/h
      - extremo (>= 75) requires cape >= 1800 J/kg
    """
    # 1. CAPE (0 - 35 pts)
    cape_val = max(0.0, cape)
    if cape_val < 300.0:
        cape_pts = (cape_val / 300.0) * 5.0
    elif cape_val < 1000.0:
        cape_pts = 5.0 + ((cape_val - 300.0) / 700.0) * 10.0  # 5 to 15 pts
    elif cape_val < 2000.0:
        cape_pts = 15.0 + ((cape_val - 1000.0) / 1000.0) * 12.0  # 15 to 27 pts
    else:
        cape_pts = min(35.0, 27.0 + ((cape_val - 2000.0) / 1000.0) * 8.0)

    # 2. Vertical Wind Shear 0-6 km (0 - 30 pts)
    # Severe supercell shear typically starts around 50-70 km/h (~14-20 m/s)
    if shear_kmh < 25.0:
        shear_pts = (shear_kmh / 25.0) * 5.0
    elif shear_kmh < 50.0:
        shear_pts = 5.0 + ((shear_kmh - 25.0) / 25.0) * 8.0  # 5 to 13 pts
    elif shear_kmh < 80.0:
        shear_pts = 13.0 + ((shear_kmh - 50.0) / 30.0) * 10.0  # 13 to 23 pts
    else:
        shear_pts = min(30.0, 23.0 + ((shear_kmh - 80.0) / 20.0) * 7.0)

    # 3. Freezing Level (0 - 15 pts)
    # Optimal hail growth and survival zone in central Argentina: 2,800 m to 4,300 m
    if freezing_level_m is None:
        frz_pts = 8.0
    else:
        fl = float(freezing_level_m)
        if 3200.0 <= fl <= 3900.0:
            frz_pts = 15.0
        elif 2800.0 <= fl < 3200.0:
            frz_pts = 8.0 + ((fl - 2800.0) / 400.0) * 7.0
        elif 3900.0 < fl <= 4300.0:
            frz_pts = 15.0 - ((fl - 3900.0) / 400.0) * 7.0
        elif 4300.0 < fl <= 4800.0:
            frz_pts = max(2.0, 8.0 - ((fl - 4300.0) / 500.0) * 6.0)
        elif fl > 4800.0:
            frz_pts = 2.0  # Excessive melting on descent
        else:
            frz_pts = 5.0  # Very low convective depth

    # 4. Convective Trigger / Lift (0 - 20 pts)
    trigger_signal = max(
        precip_prob / 6.0,             # e.g. 60% -> 10.0 pts
        min(15.0, precip_mm * 5.0),    # e.g. 1.0 mm -> 5.0 pts
        min(20.0, wrf_nearby_mm * 2.5) # e.g. 4.0 mm/h -> 10.0 pts
    )
    trig_pts = min(20.0, max(0.0, trigger_signal))

    total = cape_pts + shear_pts + frz_pts + trig_pts

    # Strict CAPE gates (anti-false-alarms: severe shear without sufficient CAPE cannot sustain severe hail)
    if cape_val < 500.0:
        total = min(total, 20.0)
    elif cape_val < 1100.0:
        total = min(total, 45.0)
    elif cape_val < 1800.0:
        total = min(total, 74.0)

    # Significant shear requirement: supercell hail requires shear >= 40 km/h for alto (>= 50)
    if shear_kmh < 40.0:
        total = min(total, 45.0)
    elif shear_kmh < 55.0 and total >= 75.0:
        total = min(total, 74.0)

    return round(min(100.0, max(0.0, total)), 1)


def score_to_level(score: float) -> str:
    if score >= 75.0:
        return "extremo"
    elif score >= 50.0:
        return "alto"
    elif score >= 25.0:
        return "moderado"
    else:
        return "sin_riesgo"


async def scan_goes19_band13(client: Optional[httpx.AsyncClient] = None) -> Dict[str, Any]:
    """
    Stage 2 Satellite Scanner:
    Downloads the latest G19-ABI-FD-BAND13 grayscale calibrated tile for Río Cuarto (z=6, x=20, y=38).
    Extracts sector pixels covering Río Cuarto and ~60 km W, SW, S.
    Returns detected cloud top temperatures and core presence.
    """
    global _SATELLITE_SCAN_CACHE, _SATELLITE_SCAN_CACHE_TIME

    now_ts = time.time()
    if _SATELLITE_SCAN_CACHE is not None and (now_ts - _SATELLITE_SCAN_CACHE_TIME) < _SATELLITE_SCAN_TTL:
        return _SATELLITE_SCAN_CACHE

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_client = True

    result: Dict[str, Any] = {
        "available": False,
        "severe_core_detected": False,
        "extreme_core_detected": False,
        "severe_pixels_count": 0,
        "extreme_pixels_count": 0,
        "severe_pixels_pct": 0.0,
        "min_brightness_temp_c": None,
        "center_brightness_temp_c": None,
        "timestamp_str": None,
    }

    try:
        # 1. Fetch latest times
        r_times = await client.get(REALEARTH_TIMES_URL, headers={"User-Agent": settings.USER_AGENT})
        if r_times.status_code != 200:
            return result

        times_data = r_times.json()
        t_list = times_data.get("G19-ABI-FD-BAND13", [])
        if not t_list:
            return result

        latest_time = t_list[-1]
        tile_url = REALEARTH_TILE_TEMPLATE.format(time=latest_time)

        # 2. Download tile
        r_tile = await client.get(tile_url, headers={"User-Agent": settings.USER_AGENT})
        if r_tile.status_code != 200 or len(r_tile.content) < 500:
            return result

        # 3. Process with Pillow and NumPy
        img = Image.open(io.BytesIO(r_tile.content)).convert("L")
        arr = np.array(img)

        if arr.shape[0] < 256 or arr.shape[1] < 256:
            return result

        # Extract sector ~60 km W, SW, S of Río Cuarto
        sector = arr[SECTOR_Y_MIN:SECTOR_Y_MAX, SECTOR_X_MIN:SECTOR_X_MAX]
        sector_size = sector.size

        severe_pixels = int(np.sum(sector >= PIXEL_THRESHOLD_SEVERE))
        extreme_pixels = int(np.sum(sector >= PIXEL_THRESHOLD_EXTREME))
        severe_pct = round((severe_pixels / sector_size) * 100.0, 2)

        # Min brightness temp (max pixel brightness)
        max_px = int(np.max(sector))
        min_temp_c = round((88.0 - max_px) / 1.51, 1)

        # Center brightness temp for clear-sky physical check (3x3 px block centered on Río Cuarto: x=143, y=63)
        center_block = arr[62:65, 142:145]
        mean_center_px = float(np.mean(center_block))
        center_brightness_temp_c = round((88.0 - mean_center_px) / 1.51, 1)

        # Detection criteria: at least 4 contiguous or total severe pixels in the sector (~16 km² cell)
        has_severe = severe_pixels >= 4
        has_extreme = extreme_pixels >= 2

        result = {
            "available": True,
            "severe_core_detected": has_severe,
            "extreme_core_detected": has_extreme,
            "severe_pixels_count": severe_pixels,
            "extreme_pixels_count": extreme_pixels,
            "severe_pixels_pct": severe_pct,
            "min_brightness_temp_c": min_temp_c,
            "center_brightness_temp_c": center_brightness_temp_c,
            "timestamp_str": latest_time,
        }

        _SATELLITE_SCAN_CACHE = result
        _SATELLITE_SCAN_CACHE_TIME = now_ts
        return result

    except Exception as exc:
        logger.warning(f"Error scanning GOES-19 Band 13: {exc}")
        return result
    finally:
        if close_client:
            await client.aclose()


async def evaluate_hail_risk(
    open_meteo_data: Dict[str, Any],
    wrf_data: Dict[str, Any],
    metar_map: Dict[str, Dict[str, Any]],
    client: Optional[httpx.AsyncClient] = None,
) -> Dict[str, Any]:
    """
    Unified 2-Stage Hail Engine evaluation:
    1. Atmospheric Potential (Stage 1) for the next 12 hours.
    2. Real-time GOES-19 Satellite Cold-Core Scanner (Stage 2).
    3. Sentinel surface observations (SAOC and SAOR) for confirmed hail.
    4. Anti-false-positive filtering: rejects high harmless cirrus.
    """
    om_hourly = open_meteo_data.get("hourly", [])
    wrf_rc = wrf_data.get("cities", {}).get("rio_cuarto", {})
    wrf_hourly_list = wrf_rc.get("hourly", [])
    wrf_map = {item["timestamp_utc"]: item for item in wrf_hourly_list}

    # Step 1: Evaluate Stage 1 for future hours (>= now up to +18h in America/Argentina/Cordoba)
    now_local = datetime.now(CORDOBA_TZ)
    future_hourly = []
    for h_item in om_hourly:
        t_utc_str = h_item.get("timestamp_utc", "")
        try:
            dt_utc = datetime.fromisoformat(t_utc_str.replace("Z", "+00:00"))
            dt_loc = dt_utc.astimezone(CORDOBA_TZ)
            if (dt_loc >= now_local - timedelta(minutes=45)) and (dt_loc <= now_local + timedelta(hours=18)):
                future_hourly.append(h_item)
        except Exception:
            pass

    # Fallback to first 18 items if future_hourly is empty (e.g. synthetic unit test fixtures)
    eval_window = future_hourly if future_hourly else (om_hourly[:18] if len(om_hourly) >= 18 else om_hourly)

    steps_scores: List[Tuple[str, float, float, float, float]] = []
    max_cape = 0.0
    max_shear = 0.0
    avg_frz_lvl = 3500.0
    frz_counts = 0
    frz_sum = 0.0

    for h_item in eval_window:
        t_utc = h_item.get("timestamp_utc")
        models = h_item.get("models", {})
        m_ifs = models.get("ecmwf_ifs025", {})
        m_icon = models.get("icon_seamless", {})
        m_gfs = models.get("gfs_seamless", {})

        # CAPE
        capes = [m.get("cape_jkg") for m in [m_ifs, m_icon, m_gfs] if m.get("cape_jkg") is not None]
        step_cape = max(capes) if capes else 0.0
        max_cape = max(max_cape, step_cape)

        # 10m wind and 500hPa wind for shear
        w10 = m_ifs.get("wind_speed_kmh") or m_icon.get("wind_speed_kmh") or 15.0
        d10 = m_ifs.get("wind_direction_deg") or m_icon.get("wind_direction_deg") or 0.0

        w500 = h_item.get("wind_speed_500hPa_kmh")
        if w500 is None:
            w500 = m_ifs.get("wind_speed_500hPa_kmh") or m_icon.get("wind_speed_500hPa_kmh") or 60.0
        d500 = h_item.get("wind_direction_500hPa_deg")
        if d500 is None:
            d500 = m_ifs.get("wind_direction_500hPa_deg") or m_icon.get("wind_direction_500hPa_deg") or 270.0

        shear = calculate_wind_shear_0_6km(w10, d10, w500, d500)
        max_shear = max(max_shear, shear)

        # Freezing level
        fl = h_item.get("freezing_level_height_m")
        if fl is None:
            fl = m_icon.get("freezing_level_height_m") or m_gfs.get("freezing_level_height_m")
        if fl is not None:
            frz_sum += fl
            frz_counts += 1

        # Precipitacion & WRF
        p_prob = max([m.get("precip_prob_pct") or 0.0 for m in [m_ifs, m_icon, m_gfs]])
        p_mm = max([m.get("precip_mm") or 0.0 for m in [m_ifs, m_icon, m_gfs]])

        wrf_step = wrf_map.get(t_utc, {})
        wrf_near = wrf_step.get("precip_nearby_max_mm") or 0.0

        step_score = compute_stage1_step_score(
            cape=step_cape,
            shear_kmh=shear,
            freezing_level_m=fl,
            precip_prob=p_prob,
            precip_mm=p_mm,
            wrf_nearby_mm=wrf_near,
        )
        steps_scores.append((t_utc, step_score, step_cape, shear, fl if fl is not None else 3500.0))

    if frz_counts > 0:
        avg_frz_lvl = round(frz_sum / frz_counts, 0)

    # Find peak score and apply convective energy gates across the evaluation window
    if steps_scores:
        best_step = max(steps_scores, key=lambda x: x[1])
        peak_score = best_step[1]
    else:
        peak_score = 0.0

    # Strict convective energy gates (CAPE-gate anti-false-alarms)
    if max_cape < 500.0:
        peak_score = min(peak_score, 20.0)
    elif max_cape < 1100.0:
        peak_score = min(peak_score, 45.0)
    elif max_cape < 1800.0:
        peak_score = min(peak_score, 74.0)

    if max_shear < 40.0:
        peak_score = min(peak_score, 45.0)

    peak_score = round(peak_score, 1)
    peak_level = score_to_level(peak_score)

    # Compute peak window formatted in Córdoba local time (e.g. "17:00 - 20:00", "22:00 - Mañana 02:00", or "Mañana 05:00 - 09:00")
    peak_window_local = "Sin ventana de riesgo"
    if peak_score >= 25.0 and steps_scores:
        threshold = max(20.0, peak_score - 15.0)
        elevated_indices = [i for i, s in enumerate(steps_scores) if s[1] >= threshold]
        if elevated_indices:
            best_idx = max(elevated_indices, key=lambda i: steps_scores[i][1])
            # Center on best_idx with a window of max 6 hours (up to 6 hourly steps)
            min_idx = min(elevated_indices)
            max_idx = max(elevated_indices)
            if (max_idx - min_idx + 1) > 6:
                start_idx = max(min_idx, best_idx - 2)
                end_idx = min(max_idx, start_idx + 5)
                if (end_idx - start_idx + 1) < 6 and start_idx > min_idx:
                    start_idx = max(min_idx, end_idx - 5)
            else:
                start_idx = min_idx
                end_idx = max_idx

            t_first = steps_scores[start_idx][0]
            t_last = steps_scores[end_idx][0]
            try:
                dt_start = datetime.fromisoformat(t_first.replace("Z", "+00:00")).astimezone(CORDOBA_TZ)
                dt_end = datetime.fromisoformat(t_last.replace("Z", "+00:00")).astimezone(CORDOBA_TZ) + timedelta(hours=1)
                if dt_start.date() > now_local.date():
                    peak_window_local = f"Mañana {dt_start.strftime('%H:00')} - {dt_end.strftime('%H:00')}"
                elif dt_end.date() > dt_start.date():
                    peak_window_local = f"{dt_start.strftime('%H:00')} - Mañana {dt_end.strftime('%H:00')}"
                else:
                    peak_window_local = f"{dt_start.strftime('%H:00')} - {dt_end.strftime('%H:00')}"
            except Exception:
                peak_window_local = "Próximas 6 horas"

    # Step 2: Surface Sentinel Stations Hail Check (SAOC and SAOR)
    sentinel_hail_detected = False
    sentinel_hail_station = None
    for icao in ["SAOC", "SAOR"]:
        obs = metar_map.get(icao, {})
        codes = [c.upper() for c in obs.get("wx_codes", [])]
        raw = (obs.get("raw_metar") or "").upper()
        if "GR" in codes or "GS" in codes or " GR " in f" {raw} " or " GS " in f" {raw} ":
            sentinel_hail_detected = True
            sentinel_hail_station = icao
            break

    # Step 3: Stage 2 GOES-19 Band 13 Satellite Scanner
    sat_scan = await scan_goes19_band13(client=client)
    goes_severe_core = sat_scan.get("severe_core_detected", False)

    # Step 4: Mandatory Anti-False-Positive Filter
    # Only confirm imminent hail if:
    # (a) Sentinel station actually reported GR/GS, OR
    # (b) Satellite severe core detected AND Stage 1 potential is >= 'moderado' (rejects high thin cirrus)
    imminent_hail_active = False
    cirrus_rejected = False

    if sentinel_hail_detected:
        imminent_hail_active = True
        explanation = (
            f"Alerta inmediata de granizo: reporte de superficie confirmado en la estación "
            f"{sentinel_hail_station} con precipitación de granizo/graupel (GR/GS)."
        )
    elif goes_severe_core:
        if peak_level in ("moderado", "alto", "extremo"):
            imminent_hail_active = True
            explanation = (
                f"Alerta satelital inmediata: núcleo convectivo severo con topes ultra-fríos "
                f"({sat_scan.get('min_brightness_temp_c')} °C) detectado en aproximación. "
                f"Ambiente atmosférico favorable con CAPE de {round(max_cape, 0)} J/kg y cizalladura de {round(max_shear, 0)} km/h."
            )
        else:
            cirrus_rejected = True
            explanation = (
                "Tope frío detectado satelitalmente pero descartado para granizo: atmósfera estable "
                f"(nivel {peak_level}, CAPE {round(max_cape, 0)} J/kg). Cirros altos inofensivos sin inestabilidad convectiva."
            )
    else:
        if peak_level in ("alto", "extremo"):
            explanation = (
                f"Potencial atmosférico {peak_level} para granizo durante la ventana {peak_window_local}. "
                f"CAPE máximo de {round(max_cape, 0)} J/kg y cizalladura 0-6 km de {round(max_shear, 0)} km/h."
            )
        elif peak_level == "moderado":
            explanation = (
                f"Riesgo moderado de tormentas convectivas aisladas ({peak_window_local}). "
                f"Cizalladura de {round(max_shear, 0)} km/h y nivel de congelamiento a {round(avg_frz_lvl, 0)} m."
            )
        else:
            explanation = "Atmósfera estable en Río Cuarto. Sin condiciones dinámicas ni inestabilidad para granizo."

    return {
        "level": peak_level,
        "score": peak_score,
        "peak_window_local": peak_window_local,
        "cape_max": round(max_cape, 0),
        "shear_0_6km_kmh": round(max_shear, 1),
        "freezing_level_m": round(avg_frz_lvl, 0),
        "goes_severe_core_detected": goes_severe_core,
        "sentinel_hail_detected": sentinel_hail_detected,
        "imminent_hail_alert": imminent_hail_active,
        "cirrus_rejected": cirrus_rejected,
        "explanation": explanation,
        "center_brightness_temp_c": sat_scan.get("center_brightness_temp_c"),
        "stage2_details": sat_scan,
    }
