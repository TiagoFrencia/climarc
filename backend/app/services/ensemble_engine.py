import math
import re
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

from ..database import save_forecast_snapshots
from .ml_corrector import ml_corrector


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
    Computes apparent temperature (°C) using Steadman's physical formula
    (Bureau of Meteorology standard):
    AT = T + 0.33 * e - 0.70 * v - 4.00
    where:
      T = Dry bulb temperature (°C)
      e = Water vapor pressure (hPa) = (rh / 100) * 6.105 * exp((17.27 * T) / (237.7 + T))
      v = Wind speed at 10m (m/s) = wind_kmh / 3.6
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

    # Check if precipitating (RA, DZ, TSRA, SHRA, GR, GS, SN, SG, PL)
    precip_patterns = ("RA", "DZ", "TSRA", "SHRA", "GR", "GS", "SN", "SG", "PL")
    is_precip = any(any(p in t for p in precip_patterns) for t in tokens)

    # WMO hierarchy:
    # WMO 99: Tormenta con granizo (EXCLUSIVAMENTE si contiene GR o GS)
    has_hail = (
        any("GR" in t or "GS" in t for t in tokens)
        or bool(re.search(r"\b(GR|GS|TSGR|\+TSGR)\b", raw))
    )
    # WMO 96: Tormenta fuerte (+TSRA / +TS sin granizo)
    has_heavy_ts = (
        any(t in ("+TSRA", "+TS") or "+TS" in t for t in tokens)
        or bool(re.search(r"\b\+TS(RA)?\b", raw))
    )
    # WMO 95: Tormenta eléctrica moderada/estándar
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
) -> Dict[str, Any]:
    """
    Blends multi-model numerical forecasts:
    - 0-48h with fresh WRF: WRF-SMN 35%, ECMWF 35%, ICON 15%, GFS 15%
    - Without WRF (or >48h): ECMWF 55%, ICON 25%, GFS 20%
    - Nowcast blending at t+0 (70% METAR / 30% ensemble) and t+1 (35% METAR / 65% ensemble)
      if METAR is < 90 min old.
    - Confidence index ('alta', 'media', 'baja') from temperature std-dev and precipitation spread.
    - Generates 72h hourly forecast and 7-day daily forecast.
    - Automatically records snapshot in forecast_snapshots for accuracy auditing.
    """
    now = reference_time or datetime.now(timezone.utc)
    current_hour_prefix = now.strftime("%Y-%m-%dT%H:00:00Z")

    om_hourly = open_meteo_data.get("hourly", [])
    om_daily = open_meteo_data.get("daily", [])

    # Find starting index in Open-Meteo corresponding to current hour or latest past hour
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

    blended_hourly: List[Dict[str, Any]] = []
    snapshots_to_save: List[Dict[str, Any]] = []
    models_active_set = set()

    # Pre-compute raw ensemble temperatures for temp_trend_3h feature
    raw_temps_series: List[float] = []
    for om_item in candidate_om_hourly:
        t_utc_pre = om_item["timestamp_utc"]
        models_om_pre = om_item.get("models", {})
        wrf_item_pre = wrf_map.get(t_utc_pre)
        use_wrf_pre = wrf_available and (wrf_item_pre is not None)
        w_dict = {"wrf": 0.35, "ecmwf": 0.35, "icon": 0.15, "gfs": 0.15} if use_wrf_pre else {"ecmwf": 0.55, "icon": 0.25, "gfs": 0.20}
        t_dict = {
            "ecmwf": models_om_pre.get("ecmwf_ifs025", {}).get("temperature_c"),
            "icon": models_om_pre.get("icon_seamless", {}).get("temperature_c"),
            "gfs": models_om_pre.get("gfs_seamless", {}).get("temperature_c"),
        }
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
        wrf_item = wrf_map.get(t_utc)

        # Decide weights
        use_wrf = wrf_available and (wrf_item is not None)
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
        if use_wrf and wrf_item:
            temps["wrf"] = wrf_item.get("temperature_c")

        # Gather precipitations
        precips = {
            "ecmwf": m_ecmwf.get("precip_mm") or 0.0,
            "icon": m_icon.get("precip_mm") or 0.0,
            "gfs": m_gfs.get("precip_mm") or 0.0,
        }
        if use_wrf and wrf_item:
            precips["wrf"] = wrf_item.get("precip_mm") or 0.0

        # Gather wind speeds
        winds = {
            "ecmwf": m_ecmwf.get("wind_speed_kmh"),
            "icon": m_icon.get("wind_speed_kmh"),
            "gfs": m_gfs.get("wind_speed_kmh"),
        }
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

        # Standardized Sea Level Pressure (QNH / pressure_msl, ~1010 hPa)
        # Note: WRF PSFC is station pressure (~958 hPa); NEVER mix station pressure with sea level QNH!
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

        # Physical coherence: if all active models forecast < 0.1 mm and WRF nearby core < 0.5 mm,
        # attenuate precip_prob_pct with a maximum cap of 35% (avoid phantom 85%+ rain with 0.0 mm)
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

        valid_precip_vals = [v for v in precips.values() if v is not None]
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
                # 70% METAR / 30% ensemble
                if m_temp is not None:
                    final_temp = round(0.70 * m_temp + 0.30 * ens_temp_adj, 1)
                    nowcast_blended = True
                if m_wind is not None:
                    final_wind = round(0.70 * m_wind + 0.30 * ens_wind_adj, 1)
                if m_rh is not None and final_rh is not None:
                    final_rh = round(0.70 * m_rh + 0.30 * final_rh, 1)
            elif step_idx == 1:
                # 35% METAR / 65% ensemble
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
        if wrf_item:
            models_comp["wrf"] = {
                "temperature_c": wrf_item.get("temperature_c"),
                "precip_mm": wrf_item.get("precip_mm"),
                "wind_speed_kmh": wrf_item.get("wind_speed_kmh"),
                "precip_nearby_max_mm": wrf_item.get("precip_nearby_max_mm"),
            }

        wrf_nearby_p = wrf_item.get("precip_nearby_max_mm") if wrf_item else None

        # Standard Steadman physical apparent temperature
        phys_app_t = calculate_apparent_temperature(final_temp, final_rh, final_wind)

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

        # Snapshot saving for first 3 hours
        if step_idx < 3:
            snapshots_to_save.append({
                "target_time_utc": t_utc,
                "predicted_temp_c": raw_ens_temp,
                "predicted_wind_kmh": raw_ens_wind,
                "predicted_precip_mm": round(ens_precip, 2),
                "model_source": "ensemble",
            })

    # Save snapshots into SQLite for accuracy auditor
    if snapshots_to_save:
        try:
            save_forecast_snapshots(city=city_key, snapshots=snapshots_to_save)
        except Exception:
            pass

    # Blend 7-day daily forecast (ECMWF 55%, ICON 25%, GFS 20%)
    daily_weights = {"ecmwf_ifs025": 0.55, "icon_seamless": 0.25, "gfs_seamless": 0.20}
    blended_daily: List[Dict[str, Any]] = []

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

        # Coherencia de min/max para daily[0] (Hoy) y daily[1] (Mañana) con el ensamble horario fusionado
        if d_idx in (0, 1) and blended_hourly:
            matching_hours = []
            for h in blended_hourly:
                t_str = h.get("timestamp_utc")
                if t_str:
                    try:
                        dt_h = datetime.fromisoformat(t_str.replace("Z", "+00:00")).astimezone(
                            timezone(timedelta(hours=-3))
                        )
                        if dt_h.strftime("%Y-%m-%d") == d_date:
                            matching_hours.append(h)
                    except Exception:
                        pass

            if matching_hours:
                h_temps = [h["temperature_c"] for h in matching_hours]
                max_t = round(max(h_temps), 1)
                min_t = round(min(h_temps), 1)

            # Para Hoy (daily[0]): coherencia estricta con current.temperature_c y METAR de pista
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

        # FIX 4.2: Normalizar weather_code si precip_prob_pct < 15 y precip_sum_mm < 0.2 y w_code >= 51
        if p_prob < 15 and p_sum < 0.2 and w_code >= 51:
            w_code = 3

        blended_daily.append({
            "date": d_date,
            "temperature_max_c": max_t,
            "temperature_min_c": min_t,
            "precipitation_sum_mm": p_sum,
            "precipitation_probability_max_pct": p_prob,
            "wind_speed_max_kmh": round(_weighted_mean(winds_max, daily_weights), 1),
            "weather_code": w_code,
        })

    # Build Current weather block
    current_block = {}
    if blended_hourly:
        h0 = blended_hourly[0]

        # FIX 3: Dew point from METAR when fresh or via Magnus-Tetens
        dew_point_c = None
        if metar_fresh and metar_data and metar_data.get("dewp_c") is not None:
            try:
                dew_point_c = round(float(metar_data["dewp_c"]), 1)
            except (ValueError, TypeError):
                dew_point_c = calculate_dew_point(h0["temperature_c"], h0["relative_humidity_pct"])
        else:
            dew_point_c = calculate_dew_point(h0["temperature_c"], h0["relative_humidity_pct"])

        # FIX 1: METAR weather code override & real precipitation detection
        metar_w_code = None
        metar_wx_raw = None
        metar_is_precip = False
        if metar_fresh and metar_data:
            metar_w_code, metar_wx_raw, metar_is_precip = parse_metar_phenomenon(metar_data)

        # If METAR has active phenomenon, override current and hourly[0] weather_code
        if metar_w_code is not None:
            h0["weather_code"] = metar_w_code

        # Is precipitating now flag
        is_precipitating_now = metar_is_precip or (h0.get("precip_mm", 0.0) >= 0.1)

        # Physical Steadman apparent temperature for current and h0
        current_app_temp = calculate_apparent_temperature(
            h0["temperature_c"],
            h0["relative_humidity_pct"],
            h0["wind_speed_kmh"],
        )
        h0["apparent_temperature_c"] = current_app_temp

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
    }


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
            # Met wind: direction FROM which wind blows
            sum_u += w * (-math.sin(rad))
            sum_v += w * (-math.cos(rad))
            total_w += w

    if total_w == 0 or (sum_u == 0 and sum_v == 0):
        return 0.0

    deg = math.degrees(math.atan2(-sum_u, -sum_v)) % 360.0
    return deg
