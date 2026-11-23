from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional
import logging

import httpx

from ..config import settings, PRECONFIGURED_CITIES
from ..database import (
    get_alert_settings,
    get_alert_state,
    set_alert_state,
    get_metar_history,
)
from .telegram_service import (
    format_alert_message,
    send_telegram_alert,
    CORDOBA_TZ,
)
from .metar_service import fetch_metar_observations
from .open_meteo_service import fetch_open_meteo_forecast
from .wrf_reader_service import read_wrf_forecast
from .smn_alerts_service import fetch_smn_alerts
from .sentinel_service import detect_approaching_front
from .hail_service import evaluate_hail_risk

logger = logging.getLogger(__name__)

SEVERITY_RANK: Dict[str, int] = {
    "verde": 0,
    "amarillo": 1,
    "naranja": 2,
    "rojo": 3,
}


def is_in_quiet_hours(
    now_cordoba: Optional[datetime] = None,
    start_hour: int = 23,
    end_hour: int = 7,
    quiet_enabled: bool = False,
) -> bool:
    """
    Evaluates whether local time in Cordoba falls within quiet hours.
    Supports both overnight ranges (e.g. 23 -> 07) and daytime ranges (e.g. 07 -> 14).
    Disabled by default.
    """
    if not quiet_enabled:
        return False

    if now_cordoba is None:
        now_cordoba = datetime.now(CORDOBA_TZ)

    current_hour = now_cordoba.hour

    if start_hour == end_hour:
        # 24-hour quiet mode
        return True
    elif start_hour < end_hour:
        # Daytime range, e.g. 07:00 to 14:00 (quiet if hour in [7..13])
        return start_hour <= current_hour < end_hour
    else:
        # Overnight range crossing midnight, e.g. 23:00 to 07:00 (quiet if hour >= 23 or hour < 7)
        return current_hour >= start_hour or current_hour < end_hour


def evaluate_alert_conditions(
    city_key: str = "rio_cuarto",
    alert_settings: Optional[Dict[str, Any]] = None,
    metar_obs: Optional[Dict[str, Any]] = None,
    metar_history: Optional[List[Dict[str, Any]]] = None,
    open_meteo_hourly: Optional[List[Dict[str, Any]]] = None,
    wrf_city_data: Optional[Dict[str, Any]] = None,
    smn_alerts: Optional[List[Dict[str, Any]]] = None,
    hail_risk: Optional[Dict[str, Any]] = None,
    sentinel_front: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluates weather conditions for city_key and categorizes alerts into:
    - Priority 1 (Critical — ignores quiet hours):
        * Imminent Stage 2 Hail / Confirmed Ground Hail (SAOC/SAOR)
        * Sentinel Approaching Front (SAOR / SAOL)
        * Severe METAR runway phenomena (TS, TSRA, GR, SQ, FC, +RA)
        * Rapid barometric drop (>= 3 hPa in 3h)
        * Official SMN alerts level naranja/rojo or ACP
        * Extreme gusts (>= 70 km/h)
        * Severe WRF 3x3 core (>= 15 mm/h)
    - Priority 2 (Preventive — respects quiet hours):
        * Stage 1 Preventive Hail Risk (level alto or extremo)
        * Imminent rain: prob >= threshold or mm >= threshold in next 2h, or WRF 3x3 >= threshold in next 2h
        * Strong gusts: >= threshold km/h in next 3h
        * Sharp thermal drop: drop >= threshold °C in <= 3h, or apparent temp <= threshold °C in next 3h
        * Convective risk: CAPE >= threshold with rain expected in next 3h
    """
    if alert_settings is None:
        alert_settings = get_alert_settings()

    alerts: List[Dict[str, Any]] = []

    # -------------------------------------------------------------
    # PRIORIDAD 1: FENÓMENOS CRÍTICOS / SEVEROS (IGNORA SILENCIO)
    # -------------------------------------------------------------

    # 0. Alerta Antigranizo Etapa 2 (inminente: GOES-19 núcleo severo o granizo en SAOC/SAOR)
    if hail_risk and (hail_risk.get("imminent_hail_alert") or hail_risk.get("sentinel_hail_detected")):
        alerts.append({
            "priority": 1,
            "level": "rojo" if hail_risk.get("sentinel_hail_detected") else "naranja",
            "type": "alerta_antigranizo_etapa2",
            "alert_key": "hail:imminent",
            "title": "Alerta Antigranizo — Inminente",
            "description": "Núcleo convectivo severo con potencial de granizo detectado en aproximación. Conviene resguardar vehículos inmediatamente.",
            "source": "ClimaRC-Antigranizo",
            "details": {
                "hail_level": hail_risk.get("level"),
                "hail_score": hail_risk.get("score"),
                "cape_max": hail_risk.get("cape_max"),
                "shear_0_6km_kmh": hail_risk.get("shear_0_6km_kmh"),
                "peak_window": hail_risk.get("peak_window_local"),
                "goes_severe_core": hail_risk.get("goes_severe_core_detected"),
                "sentinel_hail": hail_risk.get("sentinel_hail_detected"),
            },
        })

    # 0.b Alerta Frente Centinela (cuando SAOR o SAOL detectan frente en avance)
    if sentinel_front and sentinel_front.get("detected"):
        st_name = sentinel_front.get("station_name", "Villa Reynolds")
        st_icao = sentinel_front.get("station_icao", "SAOR")
        dist = sentinel_front.get("distance_km", 115)
        card = sentinel_front.get("cardinal", "SO")
        eta = sentinel_front.get("estimated_arrival_minutes", 60)
        alerts.append({
            "priority": 1,
            "level": "naranja",
            "type": "frente_centinela",
            "alert_key": f"sentinel:front:{st_icao}",
            "title": f"Aviso Centinela — Frente en avance ({st_name})",
            "description": (
                f"La estación centinela {st_name} ({st_icao}, {dist} km al {card}) reporta avance frontal severo "
                f"hacia Río Cuarto con arribo estimado en ~{eta} minutos."
            ),
            "source": "ClimaRC-Centinelas",
            "details": sentinel_front.get("details", {}),
        })

    # 1. METAR active phenomena
    if metar_obs:
        raw_ob = metar_obs.get("raw_metar", "").upper()
        wx_codes = [c.upper() for c in metar_obs.get("wx_codes", [])]
        icao = metar_obs.get("icao", "SAOC")
        temp_c = metar_obs.get("temp_c")

        # Hail (GR)
        if "GR" in wx_codes or " GR " in f" {raw_ob} ":
            alerts.append({
                "priority": 1,
                "level": "naranja",
                "type": "granizo",
                "alert_key": "metar:granizo",
                "title": "Alerta por caída de granizo en aeródromo",
                "description": f"Granizo activo reportado en la estación oficial de {icao} ({raw_ob}).",
                "source": "ClimaRC-METAR",
                "details": {"temp_c": temp_c, "raw_metar": raw_ob},
            })

        # Squall (SQ) or Tornado/Funnel Cloud (FC)
        if "SQ" in wx_codes or "FC" in wx_codes or " SQ " in f" {raw_ob} " or " FC " in f" {raw_ob} ":
            alerts.append({
                "priority": 1,
                "level": "rojo",
                "type": "viento_extremo",
                "alert_key": "metar:viento_extremo",
                "title": "Alerta máxima por fenómeno violento / turbonada",
                "description": f"Condición meteorológica extrema reportada en pista ({raw_ob}).",
                "source": "ClimaRC-METAR",
                "details": {"temp_c": temp_c, "raw_metar": raw_ob},
            })

        # Active Thunderstorm (TS / TSRA / VCTS)
        wx_str_raw = str(metar_obs.get("wx_string") or "").upper()
        has_ts = (
            any("TS" in c for c in wx_codes)
            or "TS" in wx_str_raw
            or " TS" in raw_ob
            or "TS " in raw_ob
            or "TSRA" in raw_ob
            or "VCTS" in raw_ob
        )
        if has_ts:
            is_heavy = "+TSRA" in wx_codes or "+RA" in wx_codes or "+TS" in raw_ob or "+TS" in wx_str_raw
            alerts.append({
                "priority": 1,
                "level": "naranja" if is_heavy else "amarillo",
                "type": "tormenta",
                "alert_key": "metar:tormenta",
                "title": "Tormenta eléctrica activa en aeródromo",
                "description": f"Actividad eléctrica observada en la estación {icao} ({raw_ob}).",
                "source": "ClimaRC-METAR",
                "details": {"temp_c": temp_c, "raw_metar": raw_ob},
            })
        elif "+RA" in wx_codes or "+RA" in raw_ob:
            alerts.append({
                "priority": 1,
                "level": "amarillo",
                "type": "lluvia_fuerte",
                "alert_key": "metar:lluvia_fuerte",
                "title": "Aviso de precipitación fuerte activa",
                "description": f"Lluvia intensa reportada en la estación {icao} ({raw_ob}).",
                "source": "ClimaRC-METAR",
                "details": {"temp_c": temp_c, "raw_metar": raw_ob},
            })

    # 2. Rapid barometric pressure drop (>= 3 hPa in 3h)
    if metar_history and len(metar_history) >= 2:
        current_qnh = None
        current_time = None
        for r in metar_history:
            if r.get("qnh_hpa") is not None:
                current_qnh = float(r["qnh_hpa"])
                current_time = r.get("obs_time_utc")
                break

        if current_qnh is not None and current_time:
            try:
                curr_dt = datetime.fromisoformat(current_time.replace("Z", "+00:00"))
                best_past = None
                best_diff_hours = 0.0
                for r in metar_history:
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
                            "priority": 1,
                            "level": "naranja" if drop >= 5.0 else "amarillo",
                            "type": "caida_barometrica",
                            "alert_key": "barometric:caida_rapida",
                            "title": "Aviso por caída barométrica rápida",
                            "description": (
                                f"La presión cayó {drop:.1f} hPa en ~{best_diff_hours:.1f}h "
                                f"({past_qnh:.1f} -> {current_qnh:.1f} hPa), indicando proximidad de línea de inestabilidad."
                            ),
                            "source": "ClimaRC-METAR",
                            "details": {"pressure_drop_hpa": drop, "past_qnh": past_qnh, "current_qnh": current_qnh},
                        })
            except Exception:
                pass

    # 3. Official SMN alerts (level naranja, rojo or ACP)
    if smn_alerts:
        for sa in smn_alerts:
            lvl = str(sa.get("level", "")).lower()
            t_low = sa.get("title", "").lower()
            d_low = sa.get("description", "").lower()
            is_acp = "corto plazo" in t_low or "corto plazo" in d_low or "acp" in t_low
            if lvl in ("naranja", "rojo") or is_acp:
                alert_id = sa.get("id") or sa.get("title", "oficial")
                alerts.append({
                    "priority": 1,
                    "level": lvl if lvl in ("naranja", "rojo") else "naranja",
                    "type": "smn_alerta_oficial",
                    "alert_key": f"smn:{alert_id}",
                    "title": sa.get("title", "Alerta Oficial SMN"),
                    "description": sa.get("description", "Aviso meteorológico oficial emitido por el SMN."),
                    "source": "SMN-Oficial",
                    "details": {"smn_level": lvl, "is_acp": is_acp},
                })

    # 4. Extreme forecast gusts (>= 70 km/h) in next 3 hours
    max_forecast_gust = 0.0
    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:3]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                gust = m_data.get("wind_gust_kmh")
                if gust is not None and gust > max_forecast_gust:
                    max_forecast_gust = gust

    if wrf_city_data:
        wrf_hourly = wrf_city_data.get("hourly", [])
        for wrf_item in wrf_hourly[:3]:
            w_near = wrf_item.get("wind_nearby_max_kmh")
            if w_near is not None and w_near > max_forecast_gust:
                max_forecast_gust = w_near

    if max_forecast_gust >= 70.0:
        alerts.append({
            "priority": 1,
            "level": "rojo" if max_forecast_gust >= 90.0 else "naranja",
            "type": "viento_extremo",
            "alert_key": "wind:rafagas_extremas",
            "title": "Alerta por ráfagas de viento muy fuertes",
            "description": f"Se pronostican ráfagas extremas de hasta {round(max_forecast_gust, 0)} km/h en las próximas 3 horas.",
            "source": "ClimaRC-Ensemble",
            "details": {"wind_gust_kmh": max_forecast_gust},
        })

    # 5. Severe WRF 3x3 core (>= 15 mm/h) in next 3 hours
    max_wrf_precip_nearby = 0.0
    if wrf_city_data:
        wrf_hourly = wrf_city_data.get("hourly", [])
        for wrf_item in wrf_hourly[:3]:
            p_near = wrf_item.get("precip_nearby_max_mm")
            if p_near is not None and p_near > max_wrf_precip_nearby:
                max_wrf_precip_nearby = p_near

    if max_wrf_precip_nearby >= 15.0:
        alerts.append({
            "priority": 1,
            "level": "rojo" if max_wrf_precip_nearby >= 25.0 else "naranja",
            "type": "conveccion_severa",
            "alert_key": "wrf:nucleo_severo",
            "title": "Alerta por núcleo de precipitación severo (WRF)",
            "description": (
                f"El modelo regional WRF detecta un núcleo convectivo severo de hasta {max_wrf_precip_nearby:.1f} mm/h "
                f"en la celda circundante (12x12 km) en las próximas 3 horas."
            ),
            "source": "ClimaRC-WRF",
            "details": {"wrf_nearby_max_mm": max_wrf_precip_nearby},
        })

    # -------------------------------------------------------------
    # PRIORIDAD 2: ALERTAS PREVENTIVAS (RESPETA HORARIO DE SILENCIO)
    # -------------------------------------------------------------

    # 0. Aviso preventivo por potencial de granizo (Etapa 1 nivel alto o extremo)
    if (
        hail_risk
        and hail_risk.get("level") in ("alto", "extremo")
        and not hail_risk.get("imminent_hail_alert")
    ):
        lvl = hail_risk.get("level")
        alerts.append({
            "priority": 2,
            "level": "naranja" if lvl == "extremo" else "amarillo",
            "type": "riesgo_granizo_etapa1",
            "alert_key": "hail:preventivo",
            "title": f"Aviso por potencial de granizo ({lvl})",
            "description": (
                f"Condiciones atmosféricas propicias para tormentas con granizo en la ventana {hail_risk.get('peak_window_local')}. "
                f"CAPE máximo de {hail_risk.get('cape_max')} J/kg y cizalladura de {hail_risk.get('shear_0_6km_kmh')} km/h."
            ),
            "source": "ClimaRC-Antigranizo",
            "details": {
                "hail_level": lvl,
                "hail_score": hail_risk.get("score"),
                "cape_jkg": hail_risk.get("cape_max"),
                "shear_kmh": hail_risk.get("shear_0_6km_kmh"),
                "peak_window": hail_risk.get("peak_window_local"),
            },
        })

    rain_prob_thresh = float(alert_settings.get("rain_prob_pct", 60.0))
    rain_mm_thresh = float(alert_settings.get("rain_mm", 1.5))
    wrf_nearby_thresh = float(alert_settings.get("wrf_nearby_mm", 8.0))
    gust_thresh = float(alert_settings.get("gust_kmh", 45.0))
    temp_drop_thresh = float(alert_settings.get("temp_drop_c", 6.0))
    cold_apparent_thresh = float(alert_settings.get("cold_apparent_c", 5.0))
    cape_thresh = float(alert_settings.get("cape_jkg", 1000.0))

    # 1. Imminent rain (next 2 hours)
    max_rain_prob_2h = 0.0
    max_rain_mm_2h = 0.0
    max_wrf_nearby_2h = 0.0

    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:2]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                p_prob = m_data.get("precip_prob_pct")
                if p_prob is not None and p_prob > max_rain_prob_2h:
                    max_rain_prob_2h = p_prob
                p_mm = m_data.get("precip_mm")
                if p_mm is not None and p_mm > max_rain_mm_2h:
                    max_rain_mm_2h = p_mm

    if wrf_city_data:
        wrf_hourly = wrf_city_data.get("hourly", [])
        for wrf_item in wrf_hourly[:2]:
            p_near = wrf_item.get("precip_nearby_max_mm")
            if p_near is not None and p_near > max_wrf_nearby_2h:
                max_wrf_nearby_2h = p_near

    if (
        max_rain_prob_2h >= rain_prob_thresh
        or max_rain_mm_2h >= rain_mm_thresh
        or max_wrf_nearby_2h >= wrf_nearby_thresh
    ):
        alerts.append({
            "priority": 2,
            "level": "amarillo",
            "type": "lluvia_inminente",
            "alert_key": "rain:lluvia_inminente",
            "title": "Aviso de precipitación inminente",
            "description": (
                f"Lluvia prevista en las próximas 2 horas: prob. {max_rain_prob_2h:.0f}%, "
                f"acumulado {max_rain_mm_2h:.1f} mm (WRF circundante {max_wrf_nearby_2h:.1f} mm/h)."
            ),
            "source": "ClimaRC-Ensemble",
            "details": {
                "rain_prob_pct": max_rain_prob_2h,
                "precip_mm": max_rain_mm_2h,
                "wrf_nearby_max_mm": max_wrf_nearby_2h,
            },
        })

    # 2. Strong gusts in next 3 hours (between threshold and 70 km/h)
    if gust_thresh <= max_forecast_gust < 70.0:
        alerts.append({
            "priority": 2,
            "level": "amarillo",
            "type": "viento_fuerte",
            "alert_key": "wind:rafagas_fuertes",
            "title": "Aviso de ráfagas de viento fuertes",
            "description": f"Se pronostican ráfagas de hasta {round(max_forecast_gust, 0)} km/h en las próximas 3 horas.",
            "source": "ClimaRC-Ensemble",
            "details": {"wind_gust_kmh": max_forecast_gust},
        })

    # 3. Sharp thermal change or cold apparent in next 3 hours
    # Determine base temperature: from current METAR if available, or hour 0
    current_temp = None
    if metar_obs and metar_obs.get("temp_c") is not None:
        current_temp = float(metar_obs["temp_c"])
    elif open_meteo_hourly and len(open_meteo_hourly) > 0:
        models0 = open_meteo_hourly[0].get("models", {})
        t_vals = [m["temperature_c"] for m in models0.values() if m.get("temperature_c") is not None]
        if t_vals:
            current_temp = sum(t_vals) / len(t_vals)

    min_temp_next3h = None
    min_apparent_next3h = None
    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:4]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                t = m_data.get("temperature_c")
                if t is not None:
                    if min_temp_next3h is None or t < min_temp_next3h:
                        min_temp_next3h = t
                app_t = m_data.get("apparent_temperature_c")
                if app_t is not None:
                    if min_apparent_next3h is None or app_t < min_apparent_next3h:
                        min_apparent_next3h = app_t

    if current_temp is not None and min_temp_next3h is not None:
        drop = current_temp - min_temp_next3h
        if drop >= temp_drop_thresh:
            alerts.append({
                "priority": 2,
                "level": "amarillo",
                "type": "cambio_termico",
                "alert_key": "temp:cambio_brusco",
                "title": "Aviso por cambio brusco de temperatura",
                "description": f"Se prevé un descenso de temperatura de {drop:.1f} °C en las próximas 3 horas ({current_temp:.1f} -> {min_temp_next3h:.1f} °C).",
                "source": "ClimaRC-Ensemble",
                "details": {"temp_c": current_temp, "temp_drop_c": drop, "min_temp_c": min_temp_next3h},
            })

    if min_apparent_next3h is not None and min_apparent_next3h <= cold_apparent_thresh:
        alerts.append({
            "priority": 2,
            "level": "amarillo",
            "type": "sensacion_termica_baja",
            "alert_key": "temp:sensacion_fria",
            "title": "Aviso por baja sensación térmica",
            "description": f"Sensación térmica muy baja prevista en las próximas 3 horas: mínima de {min_apparent_next3h:.1f} °C.",
            "source": "ClimaRC-Ensemble",
            "details": {"apparent_temp_c": min_apparent_next3h},
        })

    # 4. Convective risk (CAPE >= threshold with expected rain in next 3h)
    max_cape_3h = 0.0
    max_rain_3h = 0.0
    max_prob_3h = 0.0
    if open_meteo_hourly:
        for hour_item in open_meteo_hourly[:3]:
            models = hour_item.get("models", {})
            for m_key, m_data in models.items():
                c = m_data.get("cape_jkg")
                if c is not None and c > max_cape_3h:
                    max_cape_3h = c
                p = m_data.get("precip_mm")
                if p is not None and p > max_rain_3h:
                    max_rain_3h = p
                prob = m_data.get("precip_prob_pct")
                if prob is not None and prob > max_prob_3h:
                    max_prob_3h = prob

    if max_cape_3h >= cape_thresh and (max_rain_3h >= 1.0 or max_prob_3h >= 40.0):
        # Only add if not already covered by extreme WRF core >= 15
        if max_wrf_precip_nearby < 15.0:
            alerts.append({
                "priority": 2,
                "level": "naranja" if max_cape_3h >= 2000.0 else "amarillo",
                "type": "inestabilidad_convectiva",
                "alert_key": "convection:riesgo_convectivo",
                "title": "Aviso de alta inestabilidad convectiva (CAPE elevado)",
                "description": (
                    f"Atmósfera inestable con CAPE de hasta {int(max_cape_3h)} J/kg y precipitaciones previstas "
                    f"en las próximas 3 horas, propicio para desarrollo de tormentas."
                ),
                "source": "ClimaRC-OpenMeteo",
                "details": {"cape_jkg": max_cape_3h, "precip_mm": max_rain_3h, "rain_prob_pct": max_prob_3h},
            })

    return alerts


async def process_and_dispatch_alerts(
    detected_alerts: List[Dict[str, Any]],
    city_key: str = "rio_cuarto",
    alert_settings: Optional[Dict[str, Any]] = None,
    current_weather: Optional[Dict[str, Any]] = None,
    client: Optional[httpx.AsyncClient] = None,
    custom_db_path: Optional[str] = None,
    now_utc: Optional[datetime] = None,
    now_cordoba: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Filters detected alerts using Quiet Hours and SQLite Cooldown Anti-spam,
    dispatches allowed alerts to Telegram, and records state in SQLite.
    """
    if alert_settings is None:
        alert_settings = get_alert_settings(custom_db_path)

    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    if now_cordoba is None:
        now_cordoba = datetime.now(CORDOBA_TZ)

    quiet_active = is_in_quiet_hours(
        now_cordoba=now_cordoba,
        start_hour=int(alert_settings.get("quiet_start_hour", 23)),
        end_hour=int(alert_settings.get("quiet_end_hour", 7)),
        quiet_enabled=bool(alert_settings.get("quiet_enabled", False)),
    )

    cooldown_hours = float(alert_settings.get("cooldown_hours", 4.0))

    sent: List[Dict[str, Any]] = []
    omitted: List[Dict[str, Any]] = []

    for alert in detected_alerts:
        priority = alert.get("priority", 2)
        alert_key = alert.get("alert_key", "general")
        current_severity = str(alert.get("level", "amarillo")).lower()
        current_rank = SEVERITY_RANK.get(current_severity, 1)

        # 1. Quiet Hours Check: Priority 1 ignores quiet hours, Priority 2 respects it
        if priority == 2 and quiet_active:
            omitted.append({
                "alert_key": alert_key,
                "title": alert.get("title"),
                "level": current_severity,
                "priority": priority,
                "reason": "quiet_hours",
                "message": "Omitida por horario de silencio activo",
            })
            continue

        # 2. Cooldown Anti-spam Check:
        prev_state = get_alert_state(city=city_key, alert_key=alert_key, custom_path=custom_db_path)
        if prev_state and prev_state.get("last_sent_utc"):
            last_sent_str = prev_state["last_sent_utc"]
            if last_sent_str.endswith("Z"):
                last_sent_str = last_sent_str[:-1] + "+00:00"
            try:
                last_sent_dt = datetime.fromisoformat(last_sent_str)
                hours_since = (now_utc - last_sent_dt).total_seconds() / 3600.0

                prev_severity = str(prev_state.get("last_severity", "amarillo")).lower()
                prev_rank = SEVERITY_RANK.get(prev_severity, 1)

                if hours_since < cooldown_hours:
                    # Severity escalation bypass check
                    if current_rank > prev_rank:
                        logger.info(
                            f"Alerta {alert_key} escaló severidad ({prev_severity} -> {current_severity}). "
                            f"Eludiendo cooldown ({hours_since:.1f}h < {cooldown_hours:.1f}h)."
                        )
                    else:
                        omitted.append({
                            "alert_key": alert_key,
                            "title": alert.get("title"),
                            "level": current_severity,
                            "priority": priority,
                            "reason": "cooldown",
                            "hours_since_last_sent": round(hours_since, 2),
                            "cooldown_hours": cooldown_hours,
                            "message": f"Omitida por cooldown activo ({hours_since:.1f}h < {cooldown_hours:.1f}h)",
                        })
                        continue
            except Exception as e:
                logger.warning(f"Error parseando fecha last_sent_utc de {alert_key}: {e}")

        # 3. Format message and dispatch via Telegram
        city_name = "Río Cuarto" if city_key == "rio_cuarto" else "Córdoba Capital"
        station_icao = "SAOC" if city_key == "rio_cuarto" else "SACO"

        msg_html = format_alert_message(
            alert=alert,
            city_name=city_name,
            station_icao=station_icao,
            current_weather=current_weather,
        )

        send_res = await send_telegram_alert(message_html=msg_html, client=client)

        # 4. Save state in SQLite alert_state
        summary = f"{alert.get('title')}: {alert.get('description', '')[:120]}"
        set_alert_state(
            city=city_key,
            alert_key=alert_key,
            last_severity=current_severity,
            payload_summary=summary,
            last_sent_utc=now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            active=1,
            custom_path=custom_db_path,
        )

        sent.append({
            "alert_key": alert_key,
            "title": alert.get("title"),
            "level": current_severity,
            "priority": priority,
            "telegram_sent": send_res.get("sent", False),
            "telegram_reason": send_res.get("reason"),
            "telegram_message_id": send_res.get("message_id"),
        })

    return {
        "city": city_key,
        "timestamp_utc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detected_count": len(detected_alerts),
        "sent_count": len(sent),
        "omitted_count": len(omitted),
        "detected": detected_alerts,
        "sent": sent,
        "omitted": omitted,
    }


async def check_and_dispatch_cron(
    city_key: str = "rio_cuarto",
    client: Optional[httpx.AsyncClient] = None,
    custom_db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Main entry point for cron-triggered alert evaluation.
    Fetches real-time observations and forecasts for Río Cuarto, detects alerts,
    applies business logic, and sends notifications.
    """
    city_cfg = PRECONFIGURED_CITIES.get(city_key, PRECONFIGURED_CITIES["rio_cuarto"])
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        # Fetch observations & forecasts (all 4 sentinel stations in single HTTP call)
        metar_map = await fetch_metar_observations(client=client)
        metar_obs = metar_map.get(city_cfg.icao)

        metar_history = get_metar_history(city_cfg.icao, hours=6, custom_path=custom_db_path)

        open_meteo_res = await fetch_open_meteo_forecast(lat=city_cfg.lat, lon=city_cfg.lon, client=client)
        open_meteo_hourly = open_meteo_res.get("hourly", [])

        wrf_res = await read_wrf_forecast(client=client)
        wrf_city_data = wrf_res.get("cities", {}).get(city_cfg.key)

        smn_alerts_res = await fetch_smn_alerts(client=client)
        smn_alerts = smn_alerts_res.get("alerts", [])

        # Evaluate Sentinel Fronts and 2-Stage Hail Risk
        sentinel_front = detect_approaching_front(metar_map, custom_db_path=custom_db_path)
        hail_risk = await evaluate_hail_risk(open_meteo_res, wrf_res, metar_map, client=client)

        # Read settings from DB (falling back to config defaults)
        alert_settings = get_alert_settings(custom_path=custom_db_path)

        # Detect alerts
        detected = evaluate_alert_conditions(
            city_key=city_cfg.key,
            alert_settings=alert_settings,
            metar_obs=metar_obs,
            metar_history=metar_history,
            open_meteo_hourly=open_meteo_hourly,
            wrf_city_data=wrf_city_data,
            smn_alerts=smn_alerts,
            hail_risk=hail_risk,
            sentinel_front=sentinel_front,
        )

        current_weather_summary = None
        if metar_obs:
            current_weather_summary = {
                "temperature_c": metar_obs.get("temp_c"),
                "wind_speed_kmh": metar_obs.get("wind_speed_kmh"),
                "wind_gust_kmh": metar_obs.get("wind_gust_kmh"),
                "pressure_hpa": metar_obs.get("qnh_hpa"),
            }

        # Filter & Dispatch
        dispatch_result = await process_and_dispatch_alerts(
            detected_alerts=detected,
            city_key=city_cfg.key,
            alert_settings=alert_settings,
            current_weather=current_weather_summary,
            client=client,
            custom_db_path=custom_db_path,
        )

        return dispatch_result
    finally:
        if close_client:
            await client.aclose()
