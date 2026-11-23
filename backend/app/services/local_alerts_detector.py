from datetime import datetime, timezone
from typing import Dict, Any, List, Optional


def detect_local_alerts(
    city_key: str,
    metar_data: Optional[Dict[str, Any]] = None,
    metar_history_records: Optional[List[Dict[str, Any]]] = None,
    open_meteo_hourly: Optional[List[Dict[str, Any]]] = None,
    wrf_city_data: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Automated local severe alerts detector.
    Combines:
    1. Active METAR runway severe phenomena (TS, TSRA, GR, SQ, FC, +RA).
    2. Rapid barometric pressure drop (>= 3 hPa in 3 hours).
    3. Forecasted wind gusts in next 3h (>= 50 km/h amarillo, >= 70 km/h naranja).
    4. Severe convective risk: CAPE >= 1000 J/kg with rain OR WRF 3x3 precip_nearby_max_mm >= 8 mm/h in next 3h.
    """
    alerts: List[Dict[str, Any]] = []

    # 1. METAR active phenomena
    if metar_data:
        raw_ob = metar_data.get("raw_metar", "").upper()
        wx_codes = [c.upper() for c in metar_data.get("wx_codes", [])]
        icao = metar_data.get("icao", "aeródromo")

        # Hail (GR)
        if "GR" in wx_codes or " GR " in f" {raw_ob} ":
            alerts.append({
                "level": "naranja",
                "type": "granizo",
                "title": "Alerta por caída de granizo",
                "description": f"Caída de granizo activa reportada en la estación de {icao} ({raw_ob}).",
                "source": "ClimaRC-Local",
            })

        # Squall (SQ) or Funnel Cloud / Tornado (FC)
        if "SQ" in wx_codes or "FC" in wx_codes or " SQ " in f" {raw_ob} " or " FC " in f" {raw_ob} ":
            alerts.append({
                "level": "rojo",
                "type": "viento_extremo",
                "title": "Alerta máxima por turbonada / fenómeno violento",
                "description": f"Condición meteorológica extrema reportada en pista ({raw_ob}).",
                "source": "ClimaRC-Local",
            })

        # Thunderstorm (TS / TSRA / +TSRA)
        has_ts = any("TS" in c for c in wx_codes) or " TS" in raw_ob or "TSRA" in raw_ob
        if has_ts:
            is_heavy = "+TSRA" in wx_codes or "+RA" in wx_codes or "+TS" in raw_ob
            alerts.append({
                "level": "naranja" if is_heavy else "amarillo",
                "type": "tormenta",
                "title": "Tormenta eléctrica activa en aeródromo",
                "description": f"Actividad eléctrica y tormenta observada en la estación {icao} ({raw_ob}).",
                "source": "ClimaRC-Local",
            })
        elif "+RA" in wx_codes or "+RA" in raw_ob:
            alerts.append({
                "level": "amarillo",
                "type": "lluvia_fuerte",
                "title": "Aviso de precipitación fuerte activa",
                "description": f"Lluvia intensa reportada en la estación {icao} ({raw_ob}).",
                "source": "ClimaRC-Local",
            })

    # 2. Rapid barometric pressure drop (>= 3 hPa in 3 hours)
    if metar_history_records and len(metar_history_records) >= 2:
        current_qnh = None
        current_time = None
        for r in metar_history_records:
            if r.get("qnh_hpa") is not None:
                current_qnh = float(r["qnh_hpa"])
                current_time = r.get("obs_time_utc")
                break

        if current_qnh is not None and current_time:
            try:
                curr_dt = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
                # Search for record ~3 hours older (between 2.0 and 4.0 hours ago)
                best_past = None
                best_diff_hours = 0.0
                for r in metar_history_records:
                    if r.get("qnh_hpa") is None:
                        continue
                    past_t = r.get("obs_time_utc")
                    if not past_t:
                        continue
                    past_dt = datetime.fromisoformat(past_t.replace("Z", "+00:00"))
                    diff_h = (curr_dt - past_dt).total_seconds() / 3600.0
                    if 1.8 <= diff_h <= 4.0:
                        if best_past is None or abs(diff_h - 3.0) < abs(best_diff_hours - 3.0):
                            best_past = r
                            best_diff_hours = diff_h

                if best_past is not None:
                    past_qnh = float(best_past["qnh_hpa"])
                    drop = past_qnh - current_qnh
                    if drop >= 3.0:
                        alerts.append({
                            "level": "naranja" if drop >= 5.0 else "amarillo",
                            "type": "caida_barometrica",
                            "title": "Aviso por caída barométrica rápida",
                            "description": (
                                f"La presión atmosférica cayó {drop:.1f} hPa en aproximadamente {best_diff_hours:.1f} horas "
                                f"({past_qnh:.1f} -> {current_qnh:.1f} hPa), indicando proximidad de línea de inestabilidad o cambio de masa de aire."
                            ),
                            "source": "ClimaRC-Local",
                        })
            except Exception:
                pass

    # 3. Forecasted wind gusts in next 3 hours
    max_forecast_gust = 0.0

    # From Open-Meteo next 3 hours
    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:3]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                gust = m_data.get("wind_gust_kmh")
                if gust is not None and gust > max_forecast_gust:
                    max_forecast_gust = gust

    # From WRF 3x3 window next 3 hours
    if wrf_city_data:
        wrf_hourly = wrf_city_data.get("hourly", [])
        for wrf_item in wrf_hourly[:3]:
            w_near = wrf_item.get("wind_nearby_max_kmh")
            if w_near is not None and w_near > max_forecast_gust:
                max_forecast_gust = w_near

    if max_forecast_gust >= 70.0:
        alerts.append({
            "level": "naranja",
            "type": "viento_fuerte",
            "title": "Alerta por ráfagas de viento muy fuertes",
            "description": f"Se pronostican ráfagas de hasta {round(max_forecast_gust, 0)} km/h en las próximas 3 horas.",
            "source": "ClimaRC-Local",
        })
    elif max_forecast_gust >= 50.0:
        alerts.append({
            "level": "amarillo",
            "type": "viento_moderado",
            "title": "Aviso de ráfagas de viento intensas",
            "description": f"Se pronostican ráfagas de hasta {round(max_forecast_gust, 0)} km/h en las próximas 3 horas.",
            "source": "ClimaRC-Local",
        })

    # 4. Severe convective risk in next 3 hours
    max_cape = 0.0
    max_rain_expected = 0.0
    max_rain_prob = 0.0

    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:3]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                c = m_data.get("cape_jkg")
                if c is not None and c > max_cape:
                    max_cape = c
                p = m_data.get("precip_mm")
                if p is not None and p > max_rain_expected:
                    max_rain_expected = p
                prob = m_data.get("precip_prob_pct")
                if prob is not None and prob > max_rain_prob:
                    max_rain_prob = prob

    max_wrf_precip_nearby = 0.0
    if wrf_city_data:
        wrf_hourly = wrf_city_data.get("hourly", [])
        for wrf_item in wrf_hourly[:3]:
            p_near = wrf_item.get("precip_nearby_max_mm")
            if p_near is not None and p_near > max_wrf_precip_nearby:
                max_wrf_precip_nearby = p_near

    # Check convective precipitation in WRF 3x3 window (>= 8 mm/h)
    if max_wrf_precip_nearby >= 8.0:
        alerts.append({
            "level": "naranja" if (max_cape >= 1000 or max_wrf_precip_nearby >= 15.0) else "amarillo",
            "type": "conveccion_severa",
            "title": "Riesgo de tormenta convectiva severa cercana",
            "description": (
                f"El modelo regional WRF detecta núcleos de precipitación de hasta {max_wrf_precip_nearby:.1f} mm/h "
                f"en la ventana circundante (12x12 km) en las próximas 3 horas."
            ),
            "source": "ClimaRC-Local",
        })
    elif max_cape >= 1000.0 and (max_rain_expected >= 1.0 or max_rain_prob >= 40.0):
        alerts.append({
            "level": "naranja" if max_cape >= 2000.0 else "amarillo",
            "type": "inestabilidad_convectiva",
            "title": "Aviso de alta inestabilidad convectiva (CAPE elevado)",
            "description": (
                f"Atmósfera inestable con CAPE de hasta {int(max_cape)} J/kg y precipitaciones previstas "
                f"en las próximas 3 horas, propicio para tormentas eléctricas."
            ),
            "source": "ClimaRC-Local",
        })

    return alerts
