import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from ..database import save_forecast_snapshots


def generate_ensemble_forecast(
    city_key: str,
    open_meteo_data: Dict[str, Any],
    wrf_data: Dict[str, Any],
    metar_data: Optional[Dict[str, Any]] = None,
    max_hourly_steps: int = 72,
    reference_time: Optional[datetime] = None,
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

    for step_idx, om_item in enumerate(candidate_om_hourly):
        t_utc = om_item["timestamp_utc"]
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

        # Surface pressure
        pressures = {
            "ecmwf": m_ecmwf.get("surface_pressure_hpa"),
            "icon": m_icon.get("surface_pressure_hpa"),
            "gfs": m_gfs.get("surface_pressure_hpa"),
        }
        if use_wrf and wrf_item:
            pressures["wrf"] = wrf_item.get("surface_pressure_hpa")
        ens_pressure = _weighted_mean(pressures, weights)

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

        # Nowcast blending for step 0 (t+0) and step 1 (t+1)
        raw_ens_temp = round(ens_temp, 1)
        raw_ens_wind = round(ens_wind, 1)
        final_temp = raw_ens_temp
        final_wind = raw_ens_wind
        final_rh = round(ens_rh, 1) if ens_rh is not None else None
        nowcast_blended = False

        if metar_fresh and metar_data:
            m_temp = metar_data.get("temp_c")
            m_wind = metar_data.get("wind_speed_kmh")
            m_rh = metar_data.get("humidity_pct")

            if step_idx == 0:
                # 70% METAR / 30% ensemble
                if m_temp is not None:
                    final_temp = round(0.70 * m_temp + 0.30 * raw_ens_temp, 1)
                    nowcast_blended = True
                if m_wind is not None:
                    final_wind = round(0.70 * m_wind + 0.30 * raw_ens_wind, 1)
                if m_rh is not None and final_rh is not None:
                    final_rh = round(0.70 * m_rh + 0.30 * final_rh, 1)
            elif step_idx == 1:
                # 35% METAR / 65% ensemble
                if m_temp is not None:
                    final_temp = round(0.35 * m_temp + 0.65 * raw_ens_temp, 1)
                    nowcast_blended = True
                if m_wind is not None:
                    final_wind = round(0.35 * m_wind + 0.65 * raw_ens_wind, 1)
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

        hourly_record = {
            "lead_hour": step_idx,
            "timestamp_utc": t_utc,
            "temperature_c": final_temp,
            "apparent_temperature_c": round(ens_app_temp, 1) if ens_app_temp is not None else final_temp,
            "relative_humidity_pct": final_rh,
            "precip_mm": round(ens_precip, 2),
            "precip_prob_pct": round(ens_prob, 0),
            "wind_speed_kmh": final_wind,
            "wind_gust_kmh": round(ens_gust, 1),
            "wind_direction_deg": round(ens_wdir, 0),
            "surface_pressure_hpa": round(ens_pressure, 1) if ens_pressure is not None else None,
            "weather_code": weather_code,
            "cape_jkg": round(ens_cape, 0),
            "confidence": confidence,
            "temp_std_dev": round(std_t, 2),
            "nowcast_blended": nowcast_blended,
            "raw_ensemble_temp_c": raw_ens_temp,
            "raw_ensemble_wind_kmh": raw_ens_wind,
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

    for d_item in om_daily:
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

        blended_daily.append({
            "date": d_date,
            "temperature_max_c": round(_weighted_mean(max_temps, daily_weights), 1),
            "temperature_min_c": round(_weighted_mean(min_temps, daily_weights), 1),
            "precipitation_sum_mm": round(max(0.0, _weighted_mean(precip_sums, daily_weights)), 1),
            "precipitation_probability_max_pct": max(valid_probs) if valid_probs else 0.0,
            "wind_speed_max_kmh": round(_weighted_mean(winds_max, daily_weights), 1),
            "weather_code": w_code,
        })

    # Build Current weather block
    current_block = {}
    if blended_hourly:
        h0 = blended_hourly[0]
        current_block = {
            "timestamp_utc": h0["timestamp_utc"],
            "temperature_c": h0["temperature_c"],
            "apparent_temperature_c": h0["apparent_temperature_c"],
            "relative_humidity_pct": h0["relative_humidity_pct"],
            "wind_speed_kmh": h0["wind_speed_kmh"],
            "wind_gust_kmh": h0["wind_gust_kmh"],
            "wind_direction_deg": h0["wind_direction_deg"],
            "surface_pressure_hpa": h0["surface_pressure_hpa"],
            "weather_code": h0["weather_code"],
            "nowcast_blended": h0["nowcast_blended"],
            "confidence": h0["confidence"],
            "metar_station": metar_data.get("icao") if metar_data else None,
            "metar_age_minutes": metar_data.get("age_minutes") if metar_data else None,
            "metar_raw": metar_data.get("raw_metar") if metar_data else "",
        }

    return {
        "current": current_block,
        "hourly": blended_hourly,
        "daily": blended_daily,
        "models_active": sorted(list(models_active_set)),
        "wrf_available": wrf_available,
        "overall_confidence": blended_hourly[0]["confidence"] if blended_hourly else "media",
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
