import math
import re
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from ..database import save_forecast_snapshots
from .ml_corrector import ml_corrector
from .metar_service import calculate_qnh_trend, parse_sky_condition

CORDOBA_TZ = timezone(timedelta(hours=-3))


def calculate_dew_point(temp_c: Optional[float], rh_pct: Optional[float]) -> Optional[float]:
    """
    Computes dew point (°C) from temperature and relative humidity using Magnus-Tetens:
    Td = (b * alpha) / (a - alpha)
    where alpha = (a * T)/(b + T) + ln(RH / 100)
    """
    if temp_c is None or rh_pct is None:
        return None
    try:
        if rh_pct <= 0:
            return round(temp_c - 30.0, 1)
        if rh_pct >= 100.0:
            return round(temp_c, 1)
        a = 17.625
        b = 243.04
        alpha = (a * temp_c) / (b + temp_c) + math.log(rh_pct / 100.0)
        td = (b * alpha) / (a - alpha)
        return round(td, 1)
    except Exception:
        return None


def calculate_apparent_temperature(temp_c: float, rh_pct: Optional[float], wind_kmh: float) -> float:
    """
    Computes apparent temperature (°C) using Steadman's physical formula:
    AT = T + 0.33 * e - 0.70 * v - 4.00
    """
    try:
        rh = rh_pct if rh_pct is not None else 50.0
        v = max(0.0, wind_kmh / 3.6)
        e = (rh / 100.0) * 6.105 * math.exp((17.27 * temp_c) / (237.7 + temp_c))
        at = temp_c + 0.33 * e - 0.70 * v - 4.00
        return round(at, 1)
    except Exception:
        return round(temp_c, 1)


def parse_metar_phenomenon(metar_data: Optional[Dict[str, Any]]) -> Tuple[Optional[int], Optional[str], bool]:
    """
    Parses METAR weather phenomenon string/codes and maps to WMO weather code:
      - GR / GS -> WMO 99 (Tormenta con granizo — reservado exclusivamente para granizo real)
      - +TSRA / +TS -> WMO 96 (Tormenta fuerte)
      - TS / TSRA / VCTS -> WMO 95 (Tormenta eléctrica)
      - +RA / +SHRA -> WMO 65 (Lluvia fuerte)
      - RA / SHRA -> WMO 63 (Lluvia / Chaparrones)
      - DZ / -RA -> WMO 51 (Llovizna / Lluvia débil)
      - FG / BR -> WMO 45 (Niebla / Neblina)
    Returns: (wmo_code, metar_wx_raw_str, is_precipitating_now)
    """
    if not metar_data:
        return None, None, False

    tokens = set()
    wx_str = metar_data.get("wx_string")
    if wx_str:
        for p in str(wx_str).split():
            clean = p.strip().upper()
            if clean:
                tokens.add(clean)

    for c in metar_data.get("wx_codes", []) or []:
        if c:
            clean = str(c).strip().upper()
            if clean:
                tokens.add(clean)

    raw = (metar_data.get("raw_metar") or "").upper()
    found = re.findall(
        r"\b(\+|-|VC)?(?:(TS|SH|FZ|BL|DR)(RA|DZ|SN|SG|IC|PL|GR|GS|UP)?|(RA|DZ|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS))\b",
        raw,
    )
    for m in found:
        tok = "".join(m).strip().upper()
        if tok:
            tokens.add(tok)

    if not tokens:
        return None, None, False

    metar_wx_raw = wx_str if wx_str else " ".join(sorted(tokens))

    # Check if precipitating
    precip_patterns = ("RA", "DZ", "TSRA", "SHRA", "GR", "GS", "SN", "SG", "PL")
    is_precip = any(any(p in t for p in precip_patterns) for t in tokens)

    # WMO hierarchy
    has_hail = (
        any("GR" in t or "GS" in t for t in tokens)
        or bool(re.search(r"\b(GR|GS|TSGR|\+TSGR)\b", raw))
    )
    has_heavy_ts = (
        any(t in ("+TSRA", "+TS") or "+TS" in t for t in tokens)
        or bool(re.search(r"\b\+TS(RA)?\b", raw))
    )
    has_ts = (
        any("TS" in t for t in tokens)
        or bool(re.search(r"\b(TS|TSRA|VCTS|-TSRA|-TS)\b", raw))
    )

    w_code = None
    if has_hail:
        w_code = 99
    elif has_heavy_ts:
        w_code = 96
    elif has_ts:
        w_code = 95
    elif any(t in ("+RA", "+SHRA") for t in tokens) or bool(re.search(r"\b\+(RA|SHRA)\b", raw)):
        w_code = 65
    elif any(t in ("RA", "SHRA", "VCRA", "VCSH") for t in tokens):
        w_code = 63
    elif any(t in ("DZ", "-DZ", "+DZ", "-RA", "-SHRA") for t in tokens):
        w_code = 51
    elif any(t in ("FG", "BR", "FZFG", "MIFG") for t in tokens):
        w_code = 45

    return w_code, metar_wx_raw, is_precip


_ADAPTIVE_WEIGHTS_CACHE: Dict[str, Any] = {}
_ADAPTIVE_WEIGHTS_CACHE_TIME: float = 0.0
_ADAPTIVE_WEIGHTS_TTL: float = 6 * 3600.0  # 6 hours cache

STATIC_BASE_WEIGHTS = {
    "with_wrf": {
        "wrf_smn": 0.35,
        "ecmwf_ifs025": 0.35,
        "icon_seamless": 0.15,
        "gfs_seamless": 0.15,
    },
    "without_wrf": {
        "ecmwf_ifs025": 0.55,
        "icon_seamless": 0.25,
        "gfs_seamless": 0.20,
    },
    "with_wrf_aifs": {
        "wrf_smn": 0.30,
        "ecmwf_ifs025": 0.28,
        "ecmwf_aifs025": 0.15,
        "icon_seamless": 0.14,
        "gfs_seamless": 0.13,
    },
    "without_wrf_aifs": {
        "ecmwf_ifs025": 0.40,
        "ecmwf_aifs025": 0.22,
        "icon_seamless": 0.20,
        "gfs_seamless": 0.18,
    },
}


def clamp_and_renormalize_weights(
    weights: Dict[str, float],
    min_w: float = 0.08,
    max_w: float = 0.45,
    max_iter: int = 15,
) -> Dict[str, float]:
    """
    Clamps weights to [min_w, max_w] and iteratively renormalizes so sum(weights) == 1.0.
    Ensures no model dominates (max 0.45) or vanishes (min 0.08).
    """
    if not weights:
        return {}

    w = {k: max(0.001, float(v)) for k, v in weights.items()}
    total = sum(w.values())
    w = {k: v / total for k, v in w.items()}

    for _ in range(max_iter):
        clamped = {k: max(min_w, min(max_w, v)) for k, v in w.items()}
        curr_sum = sum(clamped.values())
        if abs(curr_sum - 1.0) < 1e-4:
            return {k: round(v, 4) for k, v in clamped.items()}

        fixed_keys = {k for k, v in clamped.items() if v <= min_w + 1e-5 or v >= max_w - 1e-5}
        if len(fixed_keys) == len(clamped):
            return {k: round(v / curr_sum, 4) for k, v in clamped.items()}

        remaining = 1.0 - sum(clamped[k] for k in fixed_keys)
        unfixed_sum = sum(clamped[k] for k in clamped if k not in fixed_keys)
        if unfixed_sum > 0:
            scale = remaining / unfixed_sum
            for k in clamped:
                if k not in fixed_keys:
                    clamped[k] = clamped[k] * scale
        w = clamped

    return {k: round(v, 4) for k, v in w.items()}


def get_adaptive_weights_state(
    city_key: str = "rio_cuarto",
    icao: str = "SAOC",
    force_refresh: bool = False,
    custom_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes adaptive weights based on real MAE against SAOC METAR over the past 14 days.
    Safety Safeguard:
      Only activates if at least 3 models have >= 48 hours matched against SAOC.
      Otherwise, keeps static base weights intact.
    Bounded Formula:
      Weight proportional to 1 / (mae_temp_c + 0.35)^2, clamped to [0.08, 0.45] and sum == 1.0.
    Cached for 6 hours in memory to protect Neon DB.
    """
    global _ADAPTIVE_WEIGHTS_CACHE, _ADAPTIVE_WEIGHTS_CACHE_TIME

    now_ts = time.time()
    cache_key = f"{city_key}:{icao}"
    if not force_refresh and cache_key in _ADAPTIVE_WEIGHTS_CACHE:
        if (now_ts - _ADAPTIVE_WEIGHTS_CACHE_TIME) < _ADAPTIVE_WEIGHTS_TTL:
            return _ADAPTIVE_WEIGHTS_CACHE[cache_key]

    try:
        from ..database import get_model_accuracy_leaderboard
        leaderboard = get_model_accuracy_leaderboard(
            city=city_key,
            icao=icao,
            days=14,
            custom_path=custom_path,
            database_url=database_url,
        )
    except Exception:
        leaderboard = []

    lb_map = {item["model_source"]: item for item in leaderboard}
    candidate_models = ["wrf_smn", "ecmwf_ifs025", "icon_seamless", "gfs_seamless"]
    qualified_models = [
        m for m in candidate_models
        if lb_map.get(m, {}).get("samples_count", 0) >= 48 and lb_map.get(m, {}).get("mae_temperature_c") is not None
    ]

    samples_acc = max([lb_map.get(m, {}).get("samples_count", 0) for m in candidate_models], default=0)

    if len(qualified_models) < 3:
        state = {
            "adaptive_weights_active": False,
            "min_samples_required": 48,
            "samples_accumulated": samples_acc,
            "current_weights": dict(STATIC_BASE_WEIGHTS["with_wrf"]),
            "reason": f"Solo {len(qualified_models)}/3 modelos con >= 48 muestras reales auditadas.",
        }
        _ADAPTIVE_WEIGHTS_CACHE[cache_key] = state
        _ADAPTIVE_WEIGHTS_CACHE_TIME = now_ts
        return state

    raw_scores = {}
    for m in candidate_models:
        mae = lb_map.get(m, {}).get("mae_temperature_c")
        if mae is not None:
            raw_scores[m] = 1.0 / ((float(mae) + 0.35) ** 2)
        else:
            raw_scores[m] = 1.0 / ((1.5 + 0.35) ** 2)

    clamped_weights = clamp_and_renormalize_weights(raw_scores, min_w=0.08, max_w=0.45)

    state = {
        "adaptive_weights_active": True,
        "min_samples_required": 48,
        "samples_accumulated": samples_acc,
        "current_weights": clamped_weights,
        "reason": f"{len(qualified_models)} modelos calificados con >= 48 muestras.",
    }
    _ADAPTIVE_WEIGHTS_CACHE[cache_key] = state
    _ADAPTIVE_WEIGHTS_CACHE_TIME = now_ts
    return state


def generate_ensemble_forecast(
    city_key: str,
    open_meteo_data: Dict[str, Any],
    wrf_data: Dict[str, Any],
    metar_data: Optional[Dict[str, Any]] = None,
    max_hourly_steps: int = 72,
    reference_time: Optional[datetime] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    enable_ml: bool = True,
    metar_history_records: Optional[List[Dict[str, Any]]] = None,
    center_brightness_temp_c: Optional[float] = None,
    custom_db_path: Optional[str] = None,
    database_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Blends multi-model numerical forecasts:
    - If ECMWF AIFS 0.25 (AI) has non-null data:
        * 0-48h with fresh WRF: WRF 30%, ECMWF IFS 28%, ECMWF AIFS 15%, ICON 14%, GFS 13%
        * Without WRF (or >48h): ECMWF IFS 40%, ECMWF AIFS 22%, ICON 20%, GFS 18%
    - If ECMWF AIFS 0.25 is null/unavailable (graceful fallback):
        * 0-48h with fresh WRF: WRF 35%, ECMWF IFS 35%, ICON 15%, GFS 15%
        * Without WRF (or >48h): ECMWF IFS 55%, ICON 25%, GFS 20%
    - Nowcast blending at t+0 (70% METAR / 30% ensemble) and t+1 (35% METAR / 65% ensemble) if METAR is < 90 min.
    - Calculates QNH barometric tendency and delta over the last 3 hours.
    - Incorporates UV index, sunrise and sunset local times.
    """
    now = reference_time or datetime.now(timezone.utc)
    current_hour_prefix = now.strftime("%Y-%m-%dT%H:00:00Z")

    om_hourly = open_meteo_data.get("hourly", [])
    om_daily = open_meteo_data.get("daily", [])

    # Find starting index in Open-Meteo corresponding to current hour
    start_idx = 0
    found = False
    for idx, h in enumerate(om_hourly):
        if h["timestamp_utc"] >= current_hour_prefix:
            start_idx = idx
            found = True
            break
    if not found:
        start_idx = 0

    candidate_om_hourly = om_hourly[start_idx : start_idx + max_hourly_steps]

    # Map WRF hourly records by timestamp
    wrf_city_info = wrf_data.get("cities", {}).get(city_key, {})
    wrf_hourly_list = wrf_city_info.get("hourly", [])
    wrf_map = {item["timestamp_utc"]: item for item in wrf_hourly_list}
    wrf_available = wrf_data.get("wrf_available", False)

    # Check METAR freshness (< 90 minutes)
    metar_fresh = False
    if metar_data and metar_data.get("temp_c") is not None:
        age_m = metar_data.get("age_minutes")
        if age_m is not None and age_m <= 90.0:
            metar_fresh = True

    # Detect if ECMWF AIFS 0.25 is active with valid numeric data
    aifs_active = any(
        om_item.get("models", {}).get("ecmwf_aifs025", {}).get("temperature_c") is not None
        for om_item in candidate_om_hourly[:24]
    )

    blended_hourly: List[Dict[str, Any]] = []
    snapshots_to_save: List[Dict[str, Any]] = []
    models_active_set = set()

    # Retrieve adaptive weights state (cached 6 hours, safe 48-sample gate)
    adaptive_state = get_adaptive_weights_state(
        city_key=city_key,
        icao=metar_data.get("icao", "SAOC") if metar_data else "SAOC",
        custom_path=custom_db_path,
        database_url=database_url,
    )
    is_adaptive = adaptive_state.get("adaptive_weights_active", False)
    ad_weights = adaptive_state.get("current_weights", {})

    # Pre-compute raw ensemble temperatures for temp_trend_3h feature
    raw_temps_series: List[float] = []
    for om_item in candidate_om_hourly:
        t_utc_pre = om_item["timestamp_utc"]
        models_om_pre = om_item.get("models", {})
        wrf_item_pre = wrf_map.get(t_utc_pre)
        use_wrf_pre = wrf_available and (wrf_item_pre is not None)

        if aifs_active:
            w_dict = {"wrf": 0.30, "ecmwf": 0.28, "aifs": 0.15, "icon": 0.14, "gfs": 0.13} if use_wrf_pre else {"ecmwf": 0.40, "aifs": 0.22, "icon": 0.20, "gfs": 0.18}
        else:
            w_dict = {"wrf": 0.35, "ecmwf": 0.35, "icon": 0.15, "gfs": 0.15} if use_wrf_pre else {"ecmwf": 0.55, "icon": 0.25, "gfs": 0.20}

        t_dict = {
            "ecmwf": models_om_pre.get("ecmwf_ifs025", {}).get("temperature_c"),
            "icon": models_om_pre.get("icon_seamless", {}).get("temperature_c"),
            "gfs": models_om_pre.get("gfs_seamless", {}).get("temperature_c"),
        }
        if aifs_active:
            t_dict["aifs"] = models_om_pre.get("ecmwf_aifs025", {}).get("temperature_c")
        if use_wrf_pre and wrf_item_pre:
            t_dict["wrf"] = wrf_item_pre.get("temperature_c")

        raw_temps_series.append(_weighted_mean(t_dict, w_dict))

    for step_idx, om_item in enumerate(candidate_om_hourly):
        t_utc = om_item["timestamp_utc"]
        trend_3h = round(raw_temps_series[step_idx] - raw_temps_series[step_idx - 3], 2) if step_idx >= 3 else 0.0
        models_om = om_item.get("models", {})

        m_ecmwf = models_om.get("ecmwf_ifs025", {})
        m_icon = models_om.get("icon_seamless", {})
        m_gfs = models_om.get("gfs_seamless", {})
        m_aifs = models_om.get("ecmwf_aifs025", {})
        wrf_item = wrf_map.get(t_utc)

        # Decide weights (Adaptive weights if active, otherwise static base weights)
        use_wrf = wrf_available and (wrf_item is not None)
        if is_adaptive and ad_weights:
            if aifs_active and m_aifs.get("temperature_c") is not None and "ecmwf_aifs025" in ad_weights:
                if use_wrf:
                    w_dict = {
                        "wrf": ad_weights.get("wrf_smn", 0.30),
                        "ecmwf": ad_weights.get("ecmwf_ifs025", 0.28),
                        "aifs": ad_weights.get("ecmwf_aifs025", 0.15),
                        "icon": ad_weights.get("icon_seamless", 0.14),
                        "gfs": ad_weights.get("gfs_seamless", 0.13),
                    }
                    models_active_set.add("wrf_smn")
                else:
                    w_dict = {
                        "ecmwf": ad_weights.get("ecmwf_ifs025", 0.40),
                        "aifs": ad_weights.get("ecmwf_aifs025", 0.22),
                        "icon": ad_weights.get("icon_seamless", 0.20),
                        "gfs": ad_weights.get("gfs_seamless", 0.18),
                    }
                models_active_set.add("ecmwf_aifs025")
            else:
                if use_wrf:
                    w_dict = {
                        "wrf": ad_weights.get("wrf_smn", 0.35),
                        "ecmwf": ad_weights.get("ecmwf_ifs025", 0.35),
                        "icon": ad_weights.get("icon_seamless", 0.15),
                        "gfs": ad_weights.get("gfs_seamless", 0.15),
                    }
                    models_active_set.add("wrf_smn")
                else:
                    w_dict = {
                        "ecmwf": ad_weights.get("ecmwf_ifs025", 0.55),
                        "icon": ad_weights.get("icon_seamless", 0.25),
                        "gfs": ad_weights.get("gfs_seamless", 0.20),
                    }
            tot_w = sum(w_dict.values())
            weights = {k: v / tot_w for k, v in w_dict.items()} if tot_w > 0 else w_dict
            models_active_set.update(["ecmwf_ifs025", "icon_seamless", "gfs_seamless"])
        else:
            if aifs_active and m_aifs.get("temperature_c") is not None:
                if use_wrf:
                    weights = {"wrf": 0.30, "ecmwf": 0.28, "aifs": 0.15, "icon": 0.14, "gfs": 0.13}
                    models_active_set.add("wrf_smn")
                else:
                    weights = {"ecmwf": 0.40, "aifs": 0.22, "icon": 0.20, "gfs": 0.18}
                models_active_set.add("ecmwf_aifs025")
            else:
                if use_wrf:
                    weights = {"wrf": 0.35, "ecmwf": 0.35, "icon": 0.15, "gfs": 0.15}
                    models_active_set.add("wrf_smn")
                else:
                    weights = {"ecmwf": 0.55, "icon": 0.25, "gfs": 0.20}

            models_active_set.update(["ecmwf_ifs025", "icon_seamless", "gfs_seamless"])

        # Gather temperatures
        temps = {
            "ecmwf": m_ecmwf.get("temperature_c"),
            "icon": m_icon.get("temperature_c"),
            "gfs": m_gfs.get("temperature_c"),
        }
        if aifs_active and m_aifs.get("temperature_c") is not None:
            temps["aifs"] = m_aifs.get("temperature_c")
        if use_wrf and wrf_item:
            temps["wrf"] = wrf_item.get("temperature_c")

        # Gather precipitations
        precips = {
            "ecmwf": m_ecmwf.get("precip_mm") or 0.0,
            "icon": m_icon.get("precip_mm") or 0.0,
            "gfs": m_gfs.get("precip_mm") or 0.0,
        }
        if aifs_active and m_aifs.get("precip_mm") is not None:
            precips["aifs"] = m_aifs.get("precip_mm") or 0.0
        if use_wrf and wrf_item:
            precips["wrf"] = wrf_item.get("precip_mm") or 0.0

        # Gather wind speeds
        winds = {
            "ecmwf": m_ecmwf.get("wind_speed_kmh"),
            "icon": m_icon.get("wind_speed_kmh"),
            "gfs": m_gfs.get("wind_speed_kmh"),
        }
        if aifs_active and m_aifs.get("wind_speed_kmh") is not None:
            winds["aifs"] = m_aifs.get("wind_speed_kmh")
        if use_wrf and wrf_item:
            winds["wrf"] = wrf_item.get("wind_speed_kmh")

        # Weighted temperature
        ens_temp = _weighted_mean(temps, weights)
        ens_precip = max(0.0, _weighted_mean(precips, weights))
        ens_wind = _weighted_mean(winds, weights)

        # Apparent temperature & humidity
        app_temps = {
            "ecmwf": m_ecmwf.get("apparent_temperature_c"),
            "icon": m_icon.get("apparent_temperature_c"),
            "gfs": m_gfs.get("apparent_temperature_c"),
        }
        ens_app_temp = _weighted_mean(app_temps, {"ecmwf": 0.55, "icon": 0.25, "gfs": 0.20})

        rhs = {
            "ecmwf": m_ecmwf.get("relative_humidity_pct"),
            "icon": m_icon.get("relative_humidity_pct"),
            "gfs": m_gfs.get("relative_humidity_pct"),
        }
        if use_wrf and wrf_item:
            rhs["wrf"] = wrf_item.get("relative_humidity_pct")
        ens_rh = _weighted_mean(rhs, weights)

        # Standardized Sea Level Pressure (QNH / pressure_msl)
        msl_pressures = {
            "ecmwf": m_ecmwf.get("pressure_msl_hpa") if m_ecmwf.get("pressure_msl_hpa") is not None else m_ecmwf.get("surface_pressure_hpa"),
            "icon": m_icon.get("pressure_msl_hpa") if m_icon.get("pressure_msl_hpa") is not None else m_icon.get("surface_pressure_hpa"),
            "gfs": m_gfs.get("pressure_msl_hpa") if m_gfs.get("pressure_msl_hpa") is not None else m_gfs.get("surface_pressure_hpa"),
        }
        ens_pressure_msl = _weighted_mean(msl_pressures, {"ecmwf": 0.55, "icon": 0.25, "gfs": 0.20})

        final_pressure = round(ens_pressure_msl, 1) if ens_pressure_msl is not None else None
        if metar_fresh and metar_data and metar_data.get("qnh_hpa") is not None:
            m_qnh = metar_data["qnh_hpa"]
            if step_idx == 0:
                final_pressure = round(m_qnh, 1)
            elif step_idx == 1 and ens_pressure_msl is not None:
                final_pressure = round(0.35 * m_qnh + 0.65 * ens_pressure_msl, 1)

        # Wind direction vector average
        dirs = {
            "ecmwf": m_ecmwf.get("wind_direction_deg"),
            "icon": m_icon.get("wind_direction_deg"),
            "gfs": m_gfs.get("wind_direction_deg"),
        }
        if use_wrf and wrf_item:
            dirs["wrf"] = wrf_item.get("wind_direction_deg")
        ens_wdir = _weighted_wind_direction(dirs, weights)

        # Gusts and CAPE
        gusts = [m_ecmwf.get("wind_gust_kmh"), m_icon.get("wind_gust_kmh"), m_gfs.get("wind_gust_kmh")]
        if use_wrf and wrf_item:
            gusts.append(wrf_item.get("wind_nearby_max_kmh"))
        valid_gusts = [g for g in gusts if g is not None]
        ens_gust = max(valid_gusts) if valid_gusts else ens_wind

        capes = [m_ecmwf.get("cape_jkg"), m_icon.get("cape_jkg"), m_gfs.get("cape_jkg")]
        valid_capes = [c for c in capes if c is not None]
        ens_cape = max(valid_capes) if valid_capes else 0.0

        probs = [m_ecmwf.get("precip_prob_pct"), m_icon.get("precip_prob_pct"), m_gfs.get("precip_prob_pct")]
        valid_probs = [p for p in probs if p is not None]
        ens_prob = max(valid_probs) if valid_probs else 0.0

        # Physical coherence: cap precip probability if no rain predicted
        valid_precip_vals = [v for v in precips.values() if v is not None]
        max_model_precip = max(valid_precip_vals) if valid_precip_vals else 0.0
        wrf_nearby_val = wrf_item.get("precip_nearby_max_mm") if wrf_item else 0.0
        if wrf_nearby_val is None:
            wrf_nearby_val = 0.0

        if max_model_precip < 0.1 and wrf_nearby_val < 0.5:
            ens_prob = min(ens_prob, 35.0)

        weather_code = m_ecmwf.get("weather_code") or m_icon.get("weather_code") or 0

        # Confidence Index calculation
        valid_temp_vals = [v for v in temps.values() if v is not None]
        if len(valid_temp_vals) >= 2:
            mean_t = sum(valid_temp_vals) / len(valid_temp_vals)
            var_t = sum((x - mean_t) ** 2 for x in valid_temp_vals) / len(valid_temp_vals)
            std_t = math.sqrt(var_t)
        else:
            std_t = 0.0

        precip_spread = (max(valid_precip_vals) - min(valid_precip_vals)) if valid_precip_vals else 0.0

        if std_t < 1.0 and precip_spread < 1.0:
            confidence = "alta"
        elif std_t < 2.2 and precip_spread < 3.5:
            confidence = "media"
        else:
            confidence = "baja"

        raw_ens_temp = round(ens_temp, 1)
        raw_ens_wind = round(ens_wind, 1)

        # Local ML Bias Corrector (LightGBM)
        if enable_ml:
            ml_temp_adj, ml_wind_adj, ml_applied = ml_corrector.compute_adjustments_for_step(
                timestamp_utc=t_utc,
                pred_temp_c=raw_ens_temp,
                pred_rh_pct=ens_rh,
                pred_pressure_msl=ens_pressure_msl,
                pred_precip_mm=ens_precip,
                pred_wind_kmh=raw_ens_wind,
                pred_wind_dir_deg=ens_wdir,
                temp_trend_3h=trend_3h,
                city_key=city_key,
                lat=lat,
                lon=lon,
            )
        else:
            ml_temp_adj, ml_wind_adj, ml_applied = 0.0, 0.0, False

        ens_temp_adj = raw_ens_temp + ml_temp_adj
        ens_wind_adj = max(0.0, raw_ens_wind + ml_wind_adj)

        # Nowcast blending for step 0 (t+0) and step 1 (t+1) with fresh METAR
        final_temp = round(ens_temp_adj, 1)
        final_wind = round(ens_wind_adj, 1)
        final_rh = round(ens_rh, 1) if ens_rh is not None else None
        nowcast_blended = False

        if metar_fresh and metar_data:
            m_temp = metar_data.get("temp_c")
            m_wind = metar_data.get("wind_speed_kmh")
            m_rh = metar_data.get("humidity_pct")

            if step_idx == 0:
                if m_temp is not None:
                    final_temp = round(0.70 * m_temp + 0.30 * ens_temp_adj, 1)
                    nowcast_blended = True
                if m_wind is not None:
                    final_wind = round(0.70 * m_wind + 0.30 * ens_wind_adj, 1)
                if m_rh is not None and final_rh is not None:
                    final_rh = round(0.70 * m_rh + 0.30 * final_rh, 1)
            elif step_idx == 1:
                if m_temp is not None:
                    final_temp = round(0.35 * m_temp + 0.65 * ens_temp_adj, 1)
                    nowcast_blended = True
                if m_wind is not None:
                    final_wind = round(0.35 * m_wind + 0.65 * ens_wind_adj, 1)
                if m_rh is not None and final_rh is not None:
                    final_rh = round(0.35 * m_rh + 0.65 * final_rh, 1)

        # Models comparison dictionary
        models_comp: Dict[str, Any] = {
            "ecmwf": {
                "temperature_c": m_ecmwf.get("temperature_c"),
                "precip_mm": m_ecmwf.get("precip_mm"),
                "wind_speed_kmh": m_ecmwf.get("wind_speed_kmh"),
            },
            "icon": {
                "temperature_c": m_icon.get("temperature_c"),
                "precip_mm": m_icon.get("precip_mm"),
                "wind_speed_kmh": m_icon.get("wind_speed_kmh"),
            },
            "gfs": {
                "temperature_c": m_gfs.get("temperature_c"),
                "precip_mm": m_gfs.get("precip_mm"),
                "wind_speed_kmh": m_gfs.get("wind_speed_kmh"),
            },
        }
        if m_aifs:
            models_comp["aifs"] = {
                "temperature_c": m_aifs.get("temperature_c"),
                "precip_mm": m_aifs.get("precip_mm"),
                "wind_speed_kmh": m_aifs.get("wind_speed_kmh"),
            }
        if wrf_item:
            models_comp["wrf"] = {
                "temperature_c": wrf_item.get("temperature_c"),
                "precip_mm": wrf_item.get("precip_mm"),
                "wind_speed_kmh": wrf_item.get("wind_speed_kmh"),
                "precip_nearby_max_mm": wrf_item.get("precip_nearby_max_mm"),
            }

        wrf_nearby_p = wrf_item.get("precip_nearby_max_mm") if wrf_item else None
        phys_app_t = calculate_apparent_temperature(final_temp, final_rh, final_wind)

        # UV index from hourly data
        uv_idx = om_item.get("uv_index", 0.0)

        hourly_record = {
            "lead_hour": step_idx,
            "timestamp_utc": t_utc,
            "temperature_c": final_temp,
            "apparent_temperature_c": phys_app_t,
            "relative_humidity_pct": final_rh,
            "precip_mm": round(ens_precip, 2),
            "precip_prob_pct": round(ens_prob, 0),
            "wind_speed_kmh": final_wind,
            "wind_gust_kmh": round(ens_gust, 1),
            "wind_direction_deg": round(ens_wdir, 0),
            "pressure_hpa": final_pressure,
            "surface_pressure_hpa": final_pressure,
            "weather_code": weather_code,
            "cape_jkg": round(ens_cape, 0),
            "uv_index": round(uv_idx, 1) if uv_idx is not None else 0.0,
            "wind_speed_500hPa_kmh": om_item.get("wind_speed_500hPa_kmh"),
            "wind_direction_500hPa_deg": om_item.get("wind_direction_500hPa_deg"),
            "freezing_level_height_m": om_item.get("freezing_level_height_m"),
            "confidence": confidence,
            "temp_std_dev": round(std_t, 2),
            "nowcast_blended": nowcast_blended,
            "raw_ensemble_temp_c": raw_ens_temp,
            "raw_ensemble_wind_kmh": raw_ens_wind,
            "ml_temp_adjustment_c": ml_temp_adj,
            "ml_wind_adjustment_kmh": ml_wind_adj,
            "ml_applied": ml_applied,
            "wrf_nearby_precip_max_mm": wrf_nearby_p,
            "models_comparison": models_comp,
        }
        blended_hourly.append(hourly_record)

        if step_idx < 3:
            # 1. Ensemble ML (final output after ML adjustment)
            snapshots_to_save.append({
                "target_time_utc": t_utc,
                "predicted_temp_c": final_temp,
                "predicted_wind_kmh": final_wind,
                "predicted_precip_mm": round(ens_precip, 2),
                "model_source": "ensemble_ml",
            })
            # 2. Ensemble Raw (physics weighted base before ML)
            snapshots_to_save.append({
                "target_time_utc": t_utc,
                "predicted_temp_c": raw_ens_temp,
                "predicted_wind_kmh": raw_ens_wind,
                "predicted_precip_mm": round(ens_precip, 2),
                "model_source": "ensemble_raw",
            })
            # 3. Legacy alias "ensemble"
            snapshots_to_save.append({
                "target_time_utc": t_utc,
                "predicted_temp_c": raw_ens_temp,
                "predicted_wind_kmh": raw_ens_wind,
                "predicted_precip_mm": round(ens_precip, 2),
                "model_source": "ensemble",
            })
            # 4. Individual active NWP models
            if "wrf" in models_comp and models_comp["wrf"].get("temperature_c") is not None:
                snapshots_to_save.append({
                    "target_time_utc": t_utc,
                    "predicted_temp_c": models_comp["wrf"].get("temperature_c"),
                    "predicted_wind_kmh": models_comp["wrf"].get("wind_speed_kmh"),
                    "predicted_precip_mm": round(models_comp["wrf"].get("precip_mm") or 0.0, 2),
                    "model_source": "wrf_smn",
                })
            if "ecmwf" in models_comp and models_comp["ecmwf"].get("temperature_c") is not None:
                snapshots_to_save.append({
                    "target_time_utc": t_utc,
                    "predicted_temp_c": models_comp["ecmwf"].get("temperature_c"),
                    "predicted_wind_kmh": models_comp["ecmwf"].get("wind_speed_kmh"),
                    "predicted_precip_mm": round(models_comp["ecmwf"].get("precip_mm") or 0.0, 2),
                    "model_source": "ecmwf_ifs025",
                })
            if "aifs" in models_comp and models_comp["aifs"].get("temperature_c") is not None:
                snapshots_to_save.append({
                    "target_time_utc": t_utc,
                    "predicted_temp_c": models_comp["aifs"].get("temperature_c"),
                    "predicted_wind_kmh": models_comp["aifs"].get("wind_speed_kmh"),
                    "predicted_precip_mm": round(models_comp["aifs"].get("precip_mm") or 0.0, 2),
                    "model_source": "ecmwf_aifs025",
                })
            if "icon" in models_comp and models_comp["icon"].get("temperature_c") is not None:
                snapshots_to_save.append({
                    "target_time_utc": t_utc,
                    "predicted_temp_c": models_comp["icon"].get("temperature_c"),
                    "predicted_wind_kmh": models_comp["icon"].get("wind_speed_kmh"),
                    "predicted_precip_mm": round(models_comp["icon"].get("precip_mm") or 0.0, 2),
                    "model_source": "icon_seamless",
                })
            if "gfs" in models_comp and models_comp["gfs"].get("temperature_c") is not None:
                snapshots_to_save.append({
                    "target_time_utc": t_utc,
                    "predicted_temp_c": models_comp["gfs"].get("temperature_c"),
                    "predicted_wind_kmh": models_comp["gfs"].get("wind_speed_kmh"),
                    "predicted_precip_mm": round(models_comp["gfs"].get("precip_mm") or 0.0, 2),
                    "model_source": "gfs_seamless",
                })

    if snapshots_to_save:
        try:
            save_forecast_snapshots(
                city=city_key,
                snapshots=snapshots_to_save,
                custom_path=custom_db_path,
                database_url=database_url,
            )
        except Exception:
            pass

    # Blend 7-day daily forecast
    daily_weights = {"ecmwf_ifs025": 0.55, "icon_seamless": 0.25, "gfs_seamless": 0.20}
    blended_daily: List[Dict[str, Any]] = []

    def _to_local_time_str(iso_val: Optional[str]) -> Optional[str]:
        if not iso_val:
            return None
        try:
            clean_str = iso_val.replace("Z", "")
            dt_val = datetime.fromisoformat(clean_str)
            if dt_val.tzinfo is None:
                dt_val = dt_val.replace(tzinfo=timezone.utc)
            dt_local = dt_val.astimezone(CORDOBA_TZ)
            return dt_local.strftime("%H:%M")
        except Exception:
            return iso_val[-5:] if len(iso_val) >= 5 else iso_val

    for d_idx, d_item in enumerate(om_daily):
        d_date = d_item["date"]
        d_models = d_item.get("models", {})

        m_ec = d_models.get("ecmwf_ifs025", {})
        m_ic = d_models.get("icon_seamless", {})
        m_gf = d_models.get("gfs_seamless", {})

        max_temps = {"ecmwf_ifs025": m_ec.get("temperature_max_c"), "icon_seamless": m_ic.get("temperature_max_c"), "gfs_seamless": m_gf.get("temperature_max_c")}
        min_temps = {"ecmwf_ifs025": m_ec.get("temperature_min_c"), "icon_seamless": m_ic.get("temperature_min_c"), "gfs_seamless": m_gf.get("temperature_min_c")}
        precip_sums = {"ecmwf_ifs025": m_ec.get("precipitation_sum_mm"), "icon_seamless": m_ic.get("precipitation_sum_mm"), "gfs_seamless": m_gf.get("precipitation_sum_mm")}
        winds_max = {"ecmwf_ifs025": m_ec.get("wind_speed_max_kmh"), "icon_seamless": m_ic.get("wind_speed_max_kmh"), "gfs_seamless": m_gf.get("wind_speed_max_kmh")}

        probs = [m_ec.get("precipitation_probability_max_pct"), m_ic.get("precipitation_probability_max_pct"), m_gf.get("precipitation_probability_max_pct")]
        valid_probs = [p for p in probs if p is not None]

        w_code = m_ec.get("weather_code") or m_ic.get("weather_code") or 0
        p_prob = max(valid_probs) if valid_probs else 0.0
        p_sum = round(max(0.0, _weighted_mean(precip_sums, daily_weights)), 1)
        max_t = round(_weighted_mean(max_temps, daily_weights), 1)
        min_t = round(_weighted_mean(min_temps, daily_weights), 1)

        # Min/max daily coherence with hourly steps
        if d_idx in (0, 1) and blended_hourly:
            matching_hours = []
            for h in blended_hourly:
                t_str = h.get("timestamp_utc")
                if t_str:
                    try:
                        dt_h = datetime.fromisoformat(t_str.replace("Z", "+00:00")).astimezone(CORDOBA_TZ)
                        if dt_h.strftime("%Y-%m-%d") == d_date:
                            matching_hours.append(h)
                    except Exception:
                        pass

            if matching_hours:
                h_temps = [h["temperature_c"] for h in matching_hours]
                max_t = round(max(h_temps), 1)
                min_t = round(min(h_temps), 1)

            if d_idx == 0:
                curr_temp = blended_hourly[0]["temperature_c"]
                min_t = round(min(min_t, curr_temp), 1)
                max_t = round(max(max_t, curr_temp), 1)
                if metar_fresh and metar_data and metar_data.get("temp_c") is not None:
                    try:
                        m_t = float(metar_data["temp_c"])
                        min_t = round(min(min_t, m_t), 1)
                        max_t = round(max(max_t, m_t), 1)
                    except (ValueError, TypeError):
                        pass

        if p_prob < 15 and p_sum < 0.2 and w_code >= 51:
            w_code = 3

        sunrise_loc = _to_local_time_str(d_item.get("sunrise"))
        sunset_loc = _to_local_time_str(d_item.get("sunset"))
        uv_max = d_item.get("uv_index_max")

        blended_daily.append({
            "date": d_date,
            "temperature_max_c": max_t,
            "temperature_min_c": min_t,
            "precipitation_sum_mm": p_sum,
            "precipitation_probability_max_pct": p_prob,
            "wind_speed_max_kmh": round(_weighted_mean(winds_max, daily_weights), 1),
            "weather_code": w_code,
            "sunrise_local": sunrise_loc,
            "sunset_local": sunset_loc,
            "uv_index_max": round(uv_max, 1) if uv_max is not None else None,
        })

    # Build Current weather block
    current_block = {}
    if blended_hourly:
        h0 = blended_hourly[0]

        dew_point_c = None
        if metar_fresh and metar_data and metar_data.get("dewp_c") is not None:
            try:
                dew_point_c = round(float(metar_data["dewp_c"]), 1)
            except (ValueError, TypeError):
                dew_point_c = calculate_dew_point(h0["temperature_c"], h0["relative_humidity_pct"])
        else:
            dew_point_c = calculate_dew_point(h0["temperature_c"], h0["relative_humidity_pct"])

        # METAR freshness checks
        metar_age = metar_data.get("age_minutes") if metar_data else None
        is_fresh_45 = metar_data is not None and metar_age is not None and metar_age <= 45.0
        is_fresh_60 = metar_data is not None and metar_age is not None and metar_age <= 60.0

        metar_w_code = None
        metar_wx_raw = None
        raw_has_precip = False
        if metar_data:
            metar_w_code, metar_wx_raw, raw_has_precip = parse_metar_phenomenon(metar_data)

        # Rain and storm phenomena in METAR expire strictly at 45 minutes
        # If METAR is > 45 min (e.g. 66 min), a summer storm/rain has passed and cannot force current rain
        has_rain_storm_metar = raw_has_precip or (metar_w_code is not None and metar_w_code >= 51)
        if has_rain_storm_metar and not is_fresh_45:
            metar_w_code = None
            raw_has_precip = False

        # Strict rule for 'is_precipitating_now': ONLY True if fresh METAR (< 45 min) reports runway precipitation
        # NEVER mark is_precipitating_now = True merely because numerical model forecast precip_mm >= 0.1
        is_precipitating_now = bool(is_fresh_45 and raw_has_precip)

        # Apply METAR weather code override if fresh
        if metar_w_code is not None:
            if is_fresh_45 or (metar_fresh and metar_w_code < 51):
                h0["weather_code"] = metar_w_code

        # Bidirectional METAR sky override:
        # If METAR is fresh (< 60 min), reports NO precipitation or thunderstorm,
        # and sky_condition is 'clear', 'few', or 'scattered', override numerical model rain/storm (>= 51):
        # 'clear' -> 0 (Despejado)
        # 'few' -> 1 (Mayormente despejado)
        # 'scattered' -> 2 (Parcialmente nublado)
        # 'broken' / 'overcast' (fresh < 45 min, no rain in METAR) -> lower to 3 (Nublado) unless GOES-19 has active cold core
        sky_cond = metar_data.get("sky_condition") if metar_data else None
        if sky_cond is None and metar_data:
            sky_cond = parse_sky_condition(metar_data.get("raw_metar"))

        raw_metar_str = (metar_data.get("raw_metar") or "").upper() if metar_data else ""
        has_thunder = bool(
            re.search(r"\b(TS|TSRA|VCTS|\+TSRA|-TSRA|\+TS|-TS|SQ|FC|GR|GS)\b", raw_metar_str)
            or any("TS" in str(c).upper() for c in (metar_data.get("wx_codes") or []))
        ) if metar_data else False

        if metar_data and not raw_has_precip and not has_thunder:
            if is_fresh_60 and h0["weather_code"] >= 51:
                if sky_cond == "clear":
                    h0["weather_code"] = 0
                elif sky_cond == "few":
                    h0["weather_code"] = 1
                elif sky_cond == "scattered":
                    h0["weather_code"] = 2
            elif is_fresh_45 and h0["weather_code"] >= 51 and sky_cond in ("broken", "overcast"):
                goes_cold_active = (center_brightness_temp_c is not None and center_brightness_temp_c < -25.0)
                if not goes_cold_active:
                    h0["weather_code"] = 3

        # GOES-19 Band 13 Satellite Clear Sky Physical Check:
        # If not precipitating now, and center_brightness_temp_c is available and
        # (current.temperature_c - center_brightness_temp_c) <= 8.0 °C (satellite IR is measuring warm ground / open sky,
        # not cold cloud tops of precipitating systems), and model was forcing rain/storm (>= 51):
        # Correct weather_code to 1 (or 2 if scattered) to eliminate ghost rains when front left.
        if (
            not is_precipitating_now
            and center_brightness_temp_c is not None
            and h0["weather_code"] >= 51
        ):
            temp_diff = h0["temperature_c"] - center_brightness_temp_c
            if temp_diff <= 8.0:
                h0["weather_code"] = 2 if sky_cond == "scattered" else 1

        current_app_temp = calculate_apparent_temperature(
            h0["temperature_c"],
            h0["relative_humidity_pct"],
            h0["wind_speed_kmh"],
        )
        h0["apparent_temperature_c"] = current_app_temp

        # QNH barometric tendency calculation (last 3 hours)
        qnh_trend, qnh_delta = calculate_qnh_trend(
            icao=metar_data.get("icao", "SAOC") if metar_data else "SAOC",
            current_qnh=metar_data.get("qnh_hpa") if metar_data else h0.get("pressure_hpa"),
            history_records=metar_history_records,
        )

        today_sunrise = blended_daily[0].get("sunrise_local") if blended_daily else None
        today_sunset = blended_daily[0].get("sunset_local") if blended_daily else None

        current_block = {
            "timestamp_utc": h0["timestamp_utc"],
            "temperature_c": h0["temperature_c"],
            "apparent_temperature_c": current_app_temp,
            "dew_point_c": dew_point_c,
            "relative_humidity_pct": h0["relative_humidity_pct"],
            "wind_speed_kmh": h0["wind_speed_kmh"],
            "wind_gust_kmh": h0["wind_gust_kmh"],
            "wind_direction_deg": h0["wind_direction_deg"],
            "pressure_hpa": h0["pressure_hpa"],
            "surface_pressure_hpa": h0["surface_pressure_hpa"],
            "weather_code": h0["weather_code"],
            "uv_index": h0.get("uv_index", 0.0),
            "sunrise_local": today_sunrise,
            "sunset_local": today_sunset,
            "qnh_trend": qnh_trend,
            "qnh_delta_3h_hpa": qnh_delta,
            "nowcast_blended": h0["nowcast_blended"],
            "confidence": h0["confidence"],
            "raw_ensemble_temp_c": h0["raw_ensemble_temp_c"],
            "ml_temp_adjustment_c": h0["ml_temp_adjustment_c"],
            "ml_wind_adjustment_kmh": h0["ml_wind_adjustment_kmh"],
            "ml_applied": h0["ml_applied"],
            "metar_station": metar_data.get("icao") if metar_data else None,
            "metar_age_minutes": metar_data.get("age_minutes") if metar_data else None,
            "metar_raw": metar_data.get("raw_metar") if metar_data else "",
            "metar_wx_raw": metar_wx_raw,
            "sky_condition": sky_cond,
            "center_brightness_temp_c": center_brightness_temp_c,
            "is_precipitating_now": is_precipitating_now,
        }

    return {
        "current": current_block,
        "hourly": blended_hourly,
        "daily": blended_daily,
        "models_active": sorted(list(models_active_set)),
        "wrf_available": wrf_available,
        "overall_confidence": blended_hourly[0]["confidence"] if blended_hourly else "media",
        "ml_metadata": ml_corrector.get_metadata(),
        "adaptive_weights_active": is_adaptive,
        "current_weights": adaptive_state.get("current_weights", {}),
    }


def generate_executive_summary(
    current: Dict[str, Any],
    hourly: List[Dict[str, Any]],
    daily: List[Dict[str, Any]],
    hail_risk: Optional[Dict[str, Any]] = None,
    sentinel_front: Optional[Dict[str, Any]] = None,
    alerts: Optional[List[Dict[str, Any]]] = None,
    now_local: Optional[datetime] = None,
) -> str:
    """
    Generates a deterministic 1-liner executive forecast summary in natural Argentine Spanish.
    Strictly capped at 120 characters, 0 ms latency, no external LLM.
    Respects nighttime context (>= 20:00 local) and active alerts.
    """
    # 1. Imminent hail alert
    if hail_risk and hail_risk.get("imminent_hail_alert"):
        summary = "Alerta por granizo inminente: resguardar vehículos. Tormenta severa con ráfagas."
        return summary[:120]

    # 2. Sentinel front detected approaching
    if sentinel_front and sentinel_front.get("detected"):
        station = sentinel_front.get("station_name", "Villa Reynolds")
        eta = sentinel_front.get("estimated_arrival_minutes", 60)
        summary = f"Frente frío avanzando desde {station}: ráfagas y tormenta en ~{eta} min."
        return summary[:120]

    w_code = current.get("weather_code", 0)
    is_precip = current.get("is_precipitating_now", False)
    curr_t = current.get("temperature_c")
    if now_local is None:
        now_local = datetime.now(timezone.utc).astimezone(CORDOBA_TZ)
    is_night = now_local.hour >= 20 or now_local.hour < 6

    # Evaluate active alerts and hail level
    has_severe_alert = any(a.get("level") in ("naranja", "rojo") for a in (alerts or []))
    hail_lvl = (hail_risk.get("level") or hail_risk.get("stage1_risk_level") or "sin_riesgo").lower() if hail_risk else "sin_riesgo"
    has_mod_plus_hail = hail_lvl in ("moderado", "alto", "extremo")

    # 3. Active storm or rain now (strictly requires is_precip for rain, or active severe storm)
    if is_precip and w_code >= 51:
        phenom = "Tormenta fuerte" if w_code in (96, 99) else "Tormenta" if w_code == 95 else "Lluvia"
        # Look for when it clears in the next 12h
        clear_hour = None
        for h in hourly[:12]:
            if h.get("weather_code", 0) < 50 and (h.get("precip_mm") or 0.0) < 0.1 and (h.get("precip_prob_pct") or 0) < 30:
                t_str = h.get("timestamp_utc", "")
                try:
                    dt_c = datetime.fromisoformat(t_str.replace("Z", "+00:00")).astimezone(CORDOBA_TZ)
                    clear_hour = dt_c.strftime("%H:00")
                    break
                except Exception:
                    pass

        night_min = daily[0].get("temperature_min_c") if daily else None
        if clear_hour and night_min is not None:
            summary = f"{phenom} hasta las {clear_hour}, luego mejora y baja a {int(round(night_min))} °C a la noche."
        elif clear_hour:
            summary = f"{phenom} hasta las {clear_hour}, luego cielo mejorando."
        else:
            summary = f"{phenom} activa en la zona con ambiente húmedo e inestable."
        return summary[:120]
    elif w_code in (95, 96, 99):
        # Thunderstorm in the vicinity without runway precipitation
        phenom = "Tormenta fuerte" if w_code in (96, 99) else "Tormenta"
        summary = f"{phenom} en la zona con actividad eléctrica y ambiente inestable."
        return summary[:120]

    # 4. Rain or storm expected in the upcoming 8 hours (strictly future hours > now_local)
    has_future_steps = False
    for h in hourly[:8]:
        ts = h.get("timestamp_utc", "")
        try:
            if datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(CORDOBA_TZ) > now_local:
                has_future_steps = True
                break
        except Exception:
            pass

    upcoming_rain_hour = None
    upcoming_is_storm = False
    for h in hourly[:8]:
        code = h.get("weather_code", 0)
        prob = h.get("precip_prob_pct") or 0
        t_str = h.get("timestamp_utc", "")
        try:
            dt_r = datetime.fromisoformat(t_str.replace("Z", "+00:00")).astimezone(CORDOBA_TZ)
            if has_future_steps and dt_r <= now_local:
                continue
        except Exception:
            continue

        if code in (95, 96, 99):
            upcoming_is_storm = True
            upcoming_rain_hour = dt_r.strftime("%H:00")
            break
        elif (code in (51, 61, 63, 65) or prob >= 50) and upcoming_rain_hour is None:
            upcoming_rain_hour = dt_r.strftime("%H:00")

    tomorrow_daily = daily[1] if len(daily) > 1 else (daily[0] if daily else {})
    tomorrow_max = tomorrow_daily.get("temperature_max_c") if tomorrow_daily else (daily[0].get("temperature_max_c") if daily else curr_t)
    curr_t_str = f" ({int(round(curr_t))} °C)" if curr_t is not None else ""
    tom_max_str = f"; mañana máx. {int(round(tomorrow_max))} °C" if tomorrow_max is not None else ""

    # Wind sector and intensity
    wspd = current.get("wind_speed_kmh") or 0.0
    wdir = current.get("wind_direction_deg")
    wind_sector = "norte" if wdir is not None and (wdir <= 45 or wdir >= 315) else "sur" if wdir is not None and 135 <= wdir <= 225 else "este"
    wind_intensity = "moderado" if wspd >= 18 else "leve"

    # Nighttime execution (>= 20:00 or < 06:00)
    if is_night:
        if has_severe_alert or has_mod_plus_hail:
            if upcoming_rain_hour or upcoming_is_storm:
                hour_part = f" desde las {upcoming_rain_hour}" if upcoming_rain_hour else " en la madrugada"
                summary = f"Noche inestable{curr_t_str}, riesgo de tormentas{hour_part}{tom_max_str}."
            else:
                summary = f"Noche inestable con alerta activa{curr_t_str}; vigilancia meteorológica{tom_max_str}."
        elif upcoming_rain_hour:
            phenom_txt = f"riesgo de tormentas desde las {upcoming_rain_hour}" if upcoming_is_storm else f"probables lluvias desde las {upcoming_rain_hour}"
            summary = f"Noche parcialmente nublada{curr_t_str}, {phenom_txt}{tom_max_str}."
        else:
            night_type = "templada" if curr_t is not None and curr_t >= 18 else "fresca"
            w_txt = f" y viento {wind_sector}" if wdir is not None else ""
            summary = f"Noche {night_type}{curr_t_str}, cielo mayormente despejado{w_txt}{tom_max_str}."
        if len(summary) > 120:
            summary = summary[:117] + "..."
        return summary

    # Daytime execution (< 20:00 and >= 06:00)
    if upcoming_rain_hour:
        max_t = daily[0].get("temperature_max_c") if daily else curr_t
        t_part = f", máxima de {int(round(max_t))} °C" if max_t is not None else ""
        if upcoming_is_storm:
            summary = f"Probables tormentas aisladas desde las {upcoming_rain_hour}{t_part} con viento sur."
        else:
            summary = f"Lluvias probables desde las {upcoming_rain_hour}{t_part} y descenso térmico."
        return summary[:120]

    if has_severe_alert or has_mod_plus_hail:
        max_t = daily[0].get("temperature_max_c") if daily else curr_t
        t_part = f", máxima de {int(round(max_t))} °C" if max_t is not None else ""
        summary = f"Ambiente inestable con alerta meteorológica activa{curr_t_str}{t_part}; vigilancia de tormentas."
        return summary[:120]

    # 5. Stable / fair weather daytime
    max_t = daily[0].get("temperature_max_c") if daily else curr_t
    wspd = current.get("wind_speed_kmh") or 0.0
    wdir = current.get("wind_direction_deg")
    wind_sector = "norte" if wdir is not None and (wdir <= 45 or wdir >= 315) else "sur" if wdir is not None and 135 <= wdir <= 225 else "este"
    wind_intensity = "moderado" if wspd >= 18 else "leve"

    if max_t is not None and max_t >= 28:
        summary = f"Tarde soleada hasta {int(round(max_t))} °C, viento {wind_sector} {wind_intensity} y noche templada."
    elif max_t is not None and max_t >= 20:
        summary = f"Tarde templada hasta {int(round(max_t))} °C, cielo despejado y viento {wind_sector}."
    elif max_t is not None:
        summary = f"Jornada fresca con máxima de {int(round(max_t))} °C, viento {wind_sector} y noche despejada."
    else:
        summary = "Tiempo estable y cielo mayormente despejado en la región."

    if len(summary) > 120:
        summary = summary[:117] + "..."
    return summary


def _weighted_mean(values: Dict[str, Optional[float]], weights: Dict[str, float]) -> float:
    total_val = 0.0
    total_w = 0.0
    for key, val in values.items():
        if val is not None and key in weights:
            w = weights[key]
            total_val += val * w
            total_w += w

    if total_w > 0:
        return total_val / total_w
    return 0.0


def _weighted_wind_direction(dirs: Dict[str, Optional[float]], weights: Dict[str, float]) -> float:
    sum_u = 0.0
    sum_v = 0.0
    total_w = 0.0

    for key, d in dirs.items():
        if d is not None and key in weights:
            w = weights[key]
            rad = math.radians(d)
            sum_u += w * (-math.sin(rad))
            sum_v += w * (-math.cos(rad))
            total_w += w

    if total_w == 0 or (sum_u == 0 and sum_v == 0):
        return 0.0

    deg = math.degrees(math.atan2(-sum_u, -sum_v)) % 360.0
    return deg
