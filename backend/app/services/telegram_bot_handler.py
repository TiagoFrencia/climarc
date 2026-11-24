"""
ClimaRC v2.3 - Interactive Telegram Bot Command Handler
Processes incoming commands from Telegram webhook in background:
  - /ahora: Real-time conditions, wind, gusts, QNH + 3h trend, SAOC status, summary, PWA link.
  - /noche: Configured overnight window (default 22:00 to 06:00), min temp, rain, gusts, 1-line advice.
  - /granizo: Full Hail Module report (Stage 1 Potential + Stage 2 GOES-19 live cold core & sentinels).
  - /satelite: Annotated GOES-19 infrared satellite photo composed in memory.
  - /ruta [destino]: Road weather semaphore and 3 segments (cordoba, villamaria, villamercedes, santarosa, sanluis).
  - /ayuda / /start: Command directory and quick guide.
"""

import collections
import html
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import httpx

from ..config import settings, PRECONFIGURED_CITIES
from ..database import (
    get_alert_settings,
    get_metar_history,
    get_cached_payload,
)
from .telegram_service import (
    send_telegram_alert,
    send_telegram_photo,
    send_chat_action,
    CORDOBA_TZ,
)
from .metar_service import fetch_metar_observations
from .open_meteo_service import fetch_open_meteo_forecast
from .wrf_reader_service import read_wrf_forecast
from .hail_service import evaluate_hail_risk
from .route_service import get_route_weather
from .satellite_image_service import generate_goes19_telegram_image

logger = logging.getLogger(__name__)

# Bounded in-memory LRU for Telegram update_id deduplication (protects against Render cold-start retries)
_DEDUP_CAPACITY = 2000
_PROCESSED_UPDATES: collections.OrderedDict = collections.OrderedDict()

DESTINATION_ALIASES = {
    "cordoba": "cordoba",
    "cba": "cordoba",
    "rn36": "cordoba",
    "villamaria": "villa_maria",
    "villa_maria": "villa_maria",
    "vm": "villa_maria",
    "rn158": "villa_maria",
    "villamercedes": "villa_mercedes",
    "villa_mercedes": "villa_mercedes",
    "vmr": "villa_mercedes",
    "mercedes": "villa_mercedes",
    "rn8": "villa_mercedes",
    "santarosa": "santa_rosa_calamuchita",
    "santa_rosa": "santa_rosa_calamuchita",
    "calamuchita": "santa_rosa_calamuchita",
    "rp5": "santa_rosa_calamuchita",
    "sanluis": "san_luis",
    "san_luis": "san_luis",
    "sl": "san_luis",
    "rn7": "san_luis",
}


def is_update_processed(update_id: int) -> bool:
    """Checks whether this update_id was already received and processed."""
    return update_id in _PROCESSED_UPDATES


def mark_update_processed(update_id: int) -> None:
    """Records update_id into LRU cache with timestamp."""
    if update_id in _PROCESSED_UPDATES:
        _PROCESSED_UPDATES.move_to_end(update_id)
        return
    _PROCESSED_UPDATES[update_id] = time.time()
    if len(_PROCESSED_UPDATES) > _DEDUP_CAPACITY:
        _PROCESSED_UPDATES.popitem(last=False)


def degrees_to_cardinal(deg: Optional[float]) -> str:
    """Converts wind degrees to Argentine cardinal direction."""
    if deg is None:
        return "Variable"
    val = int((deg / 22.5) + 0.5)
    directions = [
        "Norte", "Nor-Noreste", "Noreste", "Este-Noreste",
        "Este", "Este-Sudeste", "Sudeste", "Sur-Sudeste",
        "Sur", "Sur-Sudoeste", "Sudoeste", "Oeste-Sudoeste",
        "Oeste", "Oeste-Noroeste", "Noroeste", "Nor-Noroeste"
    ]
    return directions[val % 16]


# ---------------------------------------------------------------------------
# COMANDO: /ahora
# ---------------------------------------------------------------------------
async def build_ahora_response(client: Optional[httpx.AsyncClient] = None) -> str:
    """Builds formatted HTML for /ahora command."""
    now_cordoba = datetime.now(CORDOBA_TZ)
    time_str = now_cordoba.strftime("%d/%m %H:%M")

    # Fetch METAR for SAOC and recent 3h history
    metar_map = await fetch_metar_observations(station_ids=["SAOC"], client=client)
    saoc = metar_map.get("SAOC")

    history = get_metar_history(icao="SAOC", hours=3)

    temp_c = saoc.get("temp_c") if saoc else None
    wind_kmh = saoc.get("wind_speed_kmh") if saoc else None
    gust_kmh = saoc.get("wind_gust_kmh") if saoc else None
    wind_dir = saoc.get("wind_dir_deg") if saoc else None
    qnh = saoc.get("qnh_hpa") if saoc else None
    dewp = saoc.get("dewp_c") if saoc else None

    # Calculate thermal sensation if temp and wind available
    apparent_c = None
    if temp_c is not None:
        if wind_kmh is not None and temp_c <= 10.0 and wind_kmh >= 5.0:
            # Australian / SMN Wind Chill formula
            v_ms = wind_kmh / 3.6
            apparent_c = 13.12 + 0.6215 * temp_c - 11.37 * (v_ms ** 0.16) + 0.3965 * temp_c * (v_ms ** 0.16)
        elif dewp is not None and temp_c >= 20.0:
            # Humidex approximation
            apparent_c = temp_c + 0.33 * (dewp - 10.0)
        else:
            apparent_c = temp_c

    # Pressure tendency over 3 hours (unified with app and alerts service)
    tendency_str = "Estable ➡️"
    if qnh is not None and history:
        from .metar_service import calculate_qnh_trend
        trend, delta_p = calculate_qnh_trend(
            icao="SAOC",
            current_qnh=qnh,
            history_records=history,
        )
        if delta_p is not None:
            if delta_p >= 1.5:
                tendency_str = f"Subiendo (+{delta_p:.1f} hPa/3h) ↗️"
            elif delta_p <= -3.0:
                tendency_str = f"Bajando rápido ({delta_p:.1f} hPa/3h) ⏬"
            elif delta_p <= -1.5:
                tendency_str = f"Bajando ({delta_p:.1f} hPa/3h) ↘️"
            else:
                tendency_str = f"Estable ({'+' if delta_p > 0 else ''}{delta_p:.1f} hPa/3h) ➡️" if abs(delta_p) >= 0.1 else "Estable ➡️"

    # Status of SAOC runway
    cavok = saoc.get("cavok") if saoc else False
    flight_rules = saoc.get("flight_rules") if saoc else "VFR"
    wx_phenomena = saoc.get("weather_phenomena") or []
    wx_str = ", ".join(wx_phenomena) if wx_phenomena else ("CAVOK · Despejado" if cavok else "Sin fenómenos significativos")

    wind_desc = f"{wind_kmh:.0f} km/h del {degrees_to_cardinal(wind_dir)}" if wind_kmh is not None else "En calma"
    if gust_kmh and wind_kmh and gust_kmh > wind_kmh:
        wind_desc += f" (ráfagas {gust_kmh:.0f} km/h)"

    url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"

    lines = [
        "☀️ <b>CLIMA AHORA — Río Cuarto</b>",
        f"📍 <b>Estación SAOC</b> — <i>{time_str} hs</i>",
        "",
    ]

    if temp_c is not None:
        st_text = f" (Sensación: <b>{apparent_c:.1f} °C</b>)" if apparent_c is not None and abs(apparent_c - temp_c) >= 0.5 else ""
        lines.append(f"🌡️ <b>Temperatura:</b> <b>{temp_c:.1f} °C</b>{st_text}")
    if wind_kmh is not None:
        lines.append(f"💨 <b>Viento:</b> {wind_desc}")
    if qnh is not None:
        lines.append(f"🧭 <b>Presión:</b> {qnh:.1f} hPa · <i>{tendency_str}</i>")
    if dewp is not None:
        lines.append(f"💧 <b>Punto de rocío:</b> {dewp:.1f} °C")

    lines.append(f"📡 <b>Pista aérea:</b> {wx_str} ({flight_rules})")

    # One-liner summary from cache or WRF if available
    cached = get_cached_payload("weather:rio_cuarto", max_age_seconds=1800)
    summary_one_liner = cached.get("summary_one_liner") if cached else None
    if summary_one_liner:
        lines.append("")
        lines.append(f"📝 <i>{html.escape(summary_one_liner)}</i>")

    lines.append("")
    lines.append(f'📲 <a href="{html.escape(url)}">Abrir ClimaRC en vivo</a>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMANDO: /noche
# ---------------------------------------------------------------------------
async def build_noche_response(client: Optional[httpx.AsyncClient] = None) -> str:
    """Builds formatted HTML for /noche command covering the configured overnight window."""
    alert_settings = get_alert_settings()
    w_start = int(alert_settings.get("briefing_window_start", 22))
    w_end = int(alert_settings.get("briefing_window_end", 6))

    rc_cfg = PRECONFIGURED_CITIES["rio_cuarto"]
    forecast = await fetch_open_meteo_forecast(lat=rc_cfg.lat, lon=rc_cfg.lon, client=client)
    hourly_list = forecast.get("hourly", [])

    now_cordoba = datetime.now(CORDOBA_TZ)

    # Filter items that fall into the overnight/morning window
    window_items = []
    for item in hourly_list:
        t_utc_str = item.get("timestamp_utc", "")
        try:
            dt_utc = datetime.fromisoformat(t_utc_str.replace("Z", "+00:00"))
            dt_loc = dt_utc.astimezone(CORDOBA_TZ)
            # Window check:
            # If w_start > w_end (e.g. 22 to 6):
            # Target is between today w_start:00 and tomorrow w_end:00 (or if now is early morning, between yesterday w_start and today w_end)
            hr = dt_loc.hour
            is_tonight = (dt_loc.date() == now_cordoba.date() and hr >= w_start) or \
                         (dt_loc.date() == (now_cordoba + timedelta(days=1)).date() and hr <= w_end)

            if w_start <= w_end:
                # Daytime window (e.g. 06 to 14)
                is_tonight = (dt_loc.date() == now_cordoba.date() and w_start <= hr <= w_end)

            if is_tonight:
                window_items.append(item)
        except Exception:
            pass

    # Fallback to first 8 items if window_items is empty (e.g. synthetic test fixtures)
    eval_items = window_items if window_items else hourly_list[:8]

    min_temp = None
    min_app = None
    max_precip = 0.0
    max_prob = 0.0
    max_gust = 0.0

    for it in eval_items:
        models = it.get("models", {})
        m_ifs = models.get("ecmwf_ifs025", {})
        m_icon = models.get("icon_seamless", {})
        m_gfs = models.get("gfs_seamless", {})

        temps = [m.get("temp_c") for m in [m_ifs, m_icon, m_gfs] if m.get("temp_c") is not None]
        if temps:
            cur_t = min(temps)
            min_temp = cur_t if min_temp is None else min(min_temp, cur_t)

        apps = [m.get("apparent_temp_c") for m in [m_ifs, m_icon, m_gfs] if m.get("apparent_temp_c") is not None]
        if apps:
            cur_a = min(apps)
            min_app = cur_a if min_app is None else min(min_app, cur_a)

        precips = [m.get("precip_mm") or 0.0 for m in [m_ifs, m_icon, m_gfs]]
        if precips:
            max_precip = max(max_precip, max(precips))

        probs = [m.get("precip_prob_pct") or 0.0 for m in [m_ifs, m_icon, m_gfs]]
        if probs:
            max_prob = max(max_prob, max(probs))

        gusts = [m.get("wind_gust_kmh") or 0.0 for m in [m_ifs, m_icon, m_gfs]]
        if gusts:
            max_gust = max(max_gust, max(gusts))

    min_temp_val = min_temp if min_temp is not None else 15.0
    min_app_val = min_app if min_app is not None else min_temp_val

    # Practical 1-line recommendation
    if max_precip >= 5.0 or max_gust >= 55.0:
        advice = "⚠️ Riesgo de tormentas o ráfagas fuertes. Cerrar ventanas y resguardar vehículos."
    elif max_precip >= 1.0 or max_prob >= 50.0:
        advice = "🌧️ Probabilidad de chaparrones dispersos durante la madrugada. Paraguas a mano."
    elif min_temp_val <= 8.0:
        advice = "🧥 Noche bien fría. Abrigarse adecuadamente si salís temprano."
    elif min_temp_val >= 23.0:
        advice = "🔥 Noche cálida y pesada. Mantener ventilación o refrigeración."
    else:
        advice = "🍃 Noche templada y tranquila. Ideal para ventilar el hogar."

    url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"

    lines = [
        "🌙 <b>PRONÓSTICO DE LA NOCHE — Río Cuarto</b>",
        f"🕒 <i>Franja {w_start:02d}:00 a {w_end:02d}:00 hs</i>",
        "",
        f"🌡️ <b>Mínima prevista:</b> <b>{min_temp_val:.1f} °C</b> (Sensación: {min_app_val:.1f} °C)",
        f"🌧️ <b>Lluvia máx:</b> {max_precip:.1f} mm (Probabilidad: {max_prob:.0f}%)",
        f"💨 <b>Ráfagas máximas:</b> {max_gust:.0f} km/h",
        "",
        f"💡 <b>Recomendación:</b>",
        f"<i>{advice}</i>",
        "",
        f'📲 <a href="{html.escape(url)}">Ver pronóstico hora por hora</a>',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMANDO: /granizo
# ---------------------------------------------------------------------------
async def build_granizo_response(client: Optional[httpx.AsyncClient] = None) -> str:
    """Builds formatted HTML for /granizo command."""
    now_cordoba = datetime.now(CORDOBA_TZ)
    time_str = now_cordoba.strftime("%d/%m %H:%M")

    rc_cfg = PRECONFIGURED_CITIES["rio_cuarto"]
    metar_map = await fetch_metar_observations(client=client)
    om_res = await fetch_open_meteo_forecast(lat=rc_cfg.lat, lon=rc_cfg.lon, client=client)
    wrf_res = await read_wrf_forecast(client=client)

    hail = await evaluate_hail_risk(om_res, wrf_res, metar_map, client=client)

    level = (hail.get("level") or hail.get("stage1_risk_level") or "sin_riesgo").upper()
    score = float(hail.get("score") if hail.get("score") is not None else hail.get("stage1_score", 0.0))
    peak = hail.get("peak_window_local") or "Sin ventana de riesgo"
    cape = float(hail.get("cape_max") or hail.get("max_cape_jkg", 0.0))
    shear = float(hail.get("shear_0_6km_kmh") or hail.get("max_shear_0_6km_kmh", 0.0))
    fl = float(hail.get("freezing_level_m", 0.0))

    stage2_details = hail.get("stage2_details") or {}
    stage2_temp = hail.get("center_brightness_temp_c") if hail.get("center_brightness_temp_c") is not None else stage2_details.get("min_brightness_temp_c")
    stage2_severe = hail.get("goes_severe_core_detected", False) or hail.get("stage2_severe_core_detected", False)
    ground_hail = hail.get("sentinel_hail_detected", False) or hail.get("ground_hail_confirmed", False)

    # Sentinels status
    sentinels = hail.get("sentinel_observations", {})
    saor = sentinels.get("SAOR", {})
    saol = sentinels.get("SAOL", {})
    saoc = sentinels.get("SAOC", {})

    saor_wx = ", ".join(saor.get("weather_phenomena", [])) or "Normal"
    saol_wx = ", ".join(saol.get("weather_phenomena", [])) or "Normal"
    saoc_wx = ", ".join(saoc.get("weather_phenomena", [])) or "Normal"

    # Color badge
    if level == "EXTREMO":
        badge = "🔴 <b>EXTREMO</b>"
    elif level == "ALTO":
        badge = "🟠 <b>ALTO</b>"
    elif level == "MODERADO":
        badge = "🟡 <b>MODERADO</b>"
    else:
        badge = "🟢 <b>SIN RIESGO</b>"

    sat_status = "Núcleo convectivo severo detectado ⚠️" if stage2_severe else "Sin núcleos severos"
    sat_temp_str = f"{stage2_temp:.1f} °C ({sat_status})" if stage2_temp is not None else "En escaneo continuo"

    concl = "🚨 <b>ALERTA ACTIVA:</b> Granizo inminente o confirmado." if ground_hail else (
        "⚠️ <b>PRECAUCIÓN:</b> Condiciones favorables para tormentas convectivas severas." if level in ("ALTO", "EXTREMO") else (
            "ℹ️ <b>VIGILANCIA:</b> Condiciones moderadas. Seguir evolución." if level == "MODERADO" else
            "✅ <b>TRANQUILIDAD:</b> No se prevé caída de granizo en la zona de Río Cuarto."
        )
    )

    url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"

    lines = [
        "🧊 <b>MÓDULO ANTIGRANIZO — Río Cuarto y Región</b>",
        f"📅 <i>Actualizado: {time_str} hs</i>",
        "",
        "🛡️ <b>Etapa 1: Potencial Atmosférico (1 a 12h)</b>",
        f"• Nivel de riesgo: {badge} (Score: <b>{score:.0f}/100</b>)",
        f"• Ventana pico: <b>{html.escape(peak)}</b>",
        f"• CAPE máximo: {cape:.0f} J/kg",
        f"• Cizalladura 0–6 km: {shear:.0f} km/h",
        f"• Isoterma 0 °C: {fl:.0f} m",
        "",
        "🛰️ <b>Etapa 2: Escáner GOES-19 y Centinelas en Vivo</b>",
        f"• Tope infrarrojo Ch13: <b>{sat_temp_str}</b>",
        f"• Centinela SAOR (Villa Reynolds): {saor_wx}",
        f"• Centinela SAOL (Laboulaye): {saol_wx}",
        f"• Estación SAOC (Río Cuarto): {saoc_wx}",
        "",
        concl,
        "",
        f'📲 <a href="{html.escape(url)}">Ver radar y satélite interactivo</a>',
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMANDO: /ruta [destino]
# ---------------------------------------------------------------------------
async def build_ruta_response(destination_arg: str = "cordoba") -> str:
    """Builds formatted HTML for /ruta [destino] command."""
    dest_clean = destination_arg.strip().lower()
    dest_key = DESTINATION_ALIASES.get(dest_clean, "cordoba")

    route_data = await get_route_weather(origin="rio_cuarto", destination=dest_key)

    route_name = html.escape(route_data.get("route_name", "Corredor Vial"))
    status = route_data.get("global_status", "optimo")
    summary = html.escape(route_data.get("summary", ""))
    max_rain = route_data.get("max_rain_mm", 0.0)
    max_gust = route_data.get("max_gust_kmh", 0.0)

    if status == "peligroso":
        sem_icon = "🔴"
        sem_label = "PELIGROSO"
    elif status == "precaucion":
        sem_icon = "🟡"
        sem_label = "PRECAUCIÓN"
    else:
        sem_icon = "🟢"
        sem_label = "ÓPTIMO"

    url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"

    lines = [
        f"🛣️ <b>ESTADO DE RUTA — {route_name}</b>",
        f"🚦 Semáforo global: {sem_icon} <b>{sem_label}</b>",
        f"<i>{summary}</i>",
        "",
        f"📊 Máximos en corredor: Lluvia {max_rain:.1f} mm · Ráfagas {max_gust:.0f} km/h",
        "",
        "📍 <b>Tramos del trayecto:</b>",
    ]

    segments = route_data.get("segments", [])
    for seg in segments:
        s_name = html.escape(seg.get("name", ""))
        s_temp = seg.get("temperature_c", 0.0)
        s_cond = html.escape(seg.get("condition", ""))
        s_wind = seg.get("wind_speed_kmh", 0.0)
        s_rain = seg.get("rain_mm", 0.0)
        s_status = seg.get("status", "optimo")

        seg_badge = "🟢" if s_status == "optimo" else ("🟡" if s_status == "precaucion" else "🔴")
        lines.append(
            f"• {seg_badge} <b>{s_name}:</b> {s_temp:.1f}°C, {s_cond}, "
            f"viento {s_wind:.0f} km/h, precip {s_rain:.1f} mm"
        )

    lines.append("")
    lines.append(f'📲 <a href="{html.escape(url)}">Ver mapa de rutas en ClimaRC</a>')
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# COMANDO: /ayuda o /start
# ---------------------------------------------------------------------------
def build_ayuda_response() -> str:
    """Builds formatted HTML directory of bot commands."""
    url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"
    return (
        "🤖 <b>CLIMARC BOT — COMANDOS DISPONIBLES</b>\n"
        "Centro de monitoreo meteorológico de Río Cuarto y la región.\n\n"
        "☀️ /ahora — Condiciones actuales en tiempo real (SAOC)\n"
        "🌙 /noche — Pronóstico compacto de la noche y madrugada\n"
        "🧊 /granizo — Reporte del Módulo Antigranizo (Etapas 1 y 2)\n"
        "🛰️ /satelite — Foto satelital GOES-19 IR anotada en vivo\n"
        "🛣️ /ruta <i>[destino]</i> — Estado de transitabilidad de rutas\n"
        "   <i>(destinos: cordoba, villamaria, villamercedes, santarosa, sanluis)</i>\n"
        "❓ /ayuda — Muestra esta lista de comandos\n\n"
        f'📲 <a href="{html.escape(url)}">Abrir la aplicación ClimaRC</a>'
    )


# ---------------------------------------------------------------------------
# DESPACHADOR CENTRAL DE COMANDOS EN BACKGROUND
# ---------------------------------------------------------------------------
async def handle_telegram_message_background(
    message: Dict[str, Any],
    client: Optional[httpx.AsyncClient] = None,
) -> None:
    """
    Executes Telegram bot command inside FastAPI BackgroundTasks.
    Sends typing / upload_photo chat actions immediately.
    """
    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    text = (message.get("text") or "").strip()

    if not chat_id or not text:
        return

    # Extract command name and argument (supporting /cmd@botname syntax)
    parts = text.split(maxsplit=1)
    raw_cmd = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    logger.info(f"Procesando comando Telegram '{raw_cmd}' para chat_id={chat_id}")

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=15.0)
        close_client = True

    try:
        if raw_cmd == "/satelite":
            # 1. Send upload_photo chat action
            await send_chat_action(action="upload_photo", chat_id=chat_id, client=client)

            # 2. Generate composite image in memory
            photo_bytes, min_t, ts, conv = await generate_goes19_telegram_image(client=client)

            # 3. Format caption and dispatch photo
            now_cordoba = datetime.now(CORDOBA_TZ)
            time_str = now_cordoba.strftime("%d/%m %H:%M")
            url = settings.FRONTEND_URL.strip() if settings.FRONTEND_URL else "https://climarc.vercel.app"

            caption = (
                f"🛰️ <b>GOES-19 Ch13 IR · Centro del País</b>\n"
                f"📅 <i>{time_str} hs</i>  |  Tope mín: <b>{min_t:.1f} °C</b> ({conv})\n"
                f"📍 Marcadores: Río Cuarto (SAOC) y Villa Reynolds (SAOR)\n\n"
                f'📲 <a href="{html.escape(url)}">Ver satélite interactivo en ClimaRC</a>'
            )

            await send_telegram_photo(
                photo_bytes=photo_bytes,
                caption=caption,
                chat_id=chat_id,
                client=client,
            )

        else:
            # Send typing chat action for all text commands
            await send_chat_action(action="typing", chat_id=chat_id, client=client)

            if raw_cmd == "/ahora":
                reply = await build_ahora_response(client=client)
            elif raw_cmd == "/noche":
                reply = await build_noche_response(client=client)
            elif raw_cmd == "/granizo":
                reply = await build_granizo_response(client=client)
            elif raw_cmd == "/ruta":
                dest = arg if arg else "cordoba"
                reply = await build_ruta_response(destination_arg=dest)
            elif raw_cmd in ("/ayuda", "/start", "/help"):
                reply = build_ayuda_response()
            else:
                reply = (
                    f"Comando <code>{html.escape(raw_cmd)}</code> no reconocido.\n\n"
                    f"{build_ayuda_response()}"
                )

            await send_telegram_alert(message_html=reply, chat_id=chat_id, client=client)

    except Exception as exc:
        logger.error(f"Error ejecutando comando Telegram {raw_cmd}: {exc}", exc_info=True)
        try:
            err_msg = (
                "⚠️ Ocurrió un error momentáneo procesando tu solicitud.\n"
                "Por favor reintentá en unos instantes."
            )
            await send_telegram_alert(message_html=err_msg, chat_id=chat_id, client=client)
        except Exception:
            pass
    finally:
        if close_client:
            await client.aclose()
