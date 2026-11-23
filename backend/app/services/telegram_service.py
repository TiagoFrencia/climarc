import html
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    CORDOBA_TZ = ZoneInfo("America/Argentina/Cordoba")
except Exception:
    CORDOBA_TZ = timezone(timedelta(hours=-3))

SEVERITY_ICONS = {
    "rojo": "🔴",
    "naranja": "🟠",
    "amarillo": "🟡",
    "verde": "🟢",
}

SEVERITY_LABELS = {
    "rojo": "ALERTA MÁXIMA / ROJA",
    "naranja": "ALERTA SEVERA / NARANJA",
    "amarillo": "AVISO PREVENTIVO / AMARILLO",
    "verde": "CONDICIONES NORMALES",
}


def get_cordoba_now() -> datetime:
    """Returns current datetime in America/Argentina/Cordoba timezone."""
    return datetime.now(CORDOBA_TZ)


def format_alert_message(
    alert: Dict[str, Any],
    city_name: str = "Río Cuarto",
    station_icao: str = "SAOC",
    current_weather: Optional[Dict[str, Any]] = None,
    frontend_url: Optional[str] = None,
) -> str:
    """
    Formats a clean, scannable HTML alert message for Telegram.
    Includes severity, phenomenon, key readings and optional PWA link.
    """
    severity = str(alert.get("level", "amarillo")).lower()
    icon = SEVERITY_ICONS.get(severity, "🟡")
    label = SEVERITY_LABELS.get(severity, "AVISO")
    title = html.escape(alert.get("title", "Alerta Meteorológica"))
    description = html.escape(alert.get("description", ""))

    now_local = get_cordoba_now().strftime("%d/%m %H:%M")
    url = frontend_url if frontend_url is not None else settings.FRONTEND_URL

    lines = [
        f"{icon} <b>{label}</b>",
        f"📍 <b>{html.escape(city_name)}</b> ({station_icao}) — <i>{now_local} hs</i>",
        "",
        f"⚠️ <b>{title}</b>",
        f"{description}",
    ]

    details = alert.get("details", {})
    metrics_lines = []

    if details.get("temp_c") is not None:
        metrics_lines.append(f"• Temperatura: <b>{details['temp_c']:.1f} °C</b>")
    elif current_weather and current_weather.get("temperature_c") is not None:
        metrics_lines.append(f"• Temp actual: <b>{current_weather['temperature_c']:.1f} °C</b>")

    if details.get("wind_gust_kmh") is not None:
        metrics_lines.append(f"• Ráfagas previstas: <b>{details['wind_gust_kmh']:.0f} km/h</b>")

    if details.get("rain_prob_pct") is not None:
        metrics_lines.append(f"• Prob. lluvia: <b>{details['rain_prob_pct']:.0f}%</b>")

    if details.get("precip_mm") is not None:
        metrics_lines.append(f"• Precipitación: <b>{details['precip_mm']:.1f} mm</b>")

    if details.get("wrf_nearby_max_mm") is not None:
        metrics_lines.append(f"• Núcleo WRF 3x3 (12km): <b>{details['wrf_nearby_max_mm']:.1f} mm/h</b>")

    if details.get("pressure_drop_hpa") is not None:
        metrics_lines.append(f"• Caída barométrica: <b>{details['pressure_drop_hpa']:.1f} hPa</b>")

    if details.get("cape_jkg") is not None:
        metrics_lines.append(f"• CAPE convectivo: <b>{details['cape_jkg']:.0f} J/kg</b>")

    if metrics_lines:
        lines.append("")
        lines.append("📊 <b>Valores destacados:</b>")
        lines.extend(metrics_lines)

    if url and url.strip():
        clean_url = html.escape(url.strip())
        lines.append("")
        lines.append(f'📲 <a href="{clean_url}">Abrir ClimaRC en vivo</a>')

    return "\n".join(lines)


def format_test_message(
    city_name: str = "Río Cuarto",
    station_icao: str = "SAOC",
    current_weather: Optional[Dict[str, Any]] = None,
    frontend_url: Optional[str] = None,
) -> str:
    """Formats a test alert message for user verification."""
    now_local = get_cordoba_now().strftime("%d/%m %H:%M")
    url = frontend_url if frontend_url is not None else settings.FRONTEND_URL

    lines = [
        "🧪 <b>CLIMARC — PRUEBA DE ALERTA TELEGRAM</b>",
        f"📍 <b>{html.escape(city_name)}</b> ({station_icao}) — <i>{now_local} hs</i>",
        "✅ El bot de alertas proactivas está conectado y operativo.",
    ]

    if current_weather:
        temp = current_weather.get("temperature_c")
        wind = current_weather.get("wind_speed_kmh")
        gust = current_weather.get("wind_gust_kmh")
        press = current_weather.get("pressure_hpa") or current_weather.get("surface_pressure_hpa")

        reading_parts = []
        if temp is not None:
            reading_parts.append(f"🌡️ {temp:.1f} °C")
        if wind is not None:
            w_str = f"💨 {wind:.0f} km/h"
            if gust and gust > wind:
                w_str += f" (ráfagas {gust:.0f} km/h)"
            reading_parts.append(w_str)
        if press is not None:
            reading_parts.append(f"🧭 {press:.1f} hPa")

        if reading_parts:
            lines.append("")
            lines.append(f"Lectura actual: {' | '.join(reading_parts)}")

    if url and url.strip():
        clean_url = html.escape(url.strip())
        lines.append("")
        lines.append(f'📲 <a href="{clean_url}">Ver radar y pronóstico en ClimaRC</a>')

    return "\n".join(lines)


async def send_telegram_alert(
    message_html: str,
    client: Optional[httpx.AsyncClient] = None,
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Sends an HTML message to Telegram via Bot API with 10s timeout.
    If token or chat_id is missing, gracefully returns {"sent": False, "reason": "telegram_not_configured"}.
    """
    bot_token = token if token is not None else settings.TELEGRAM_BOT_TOKEN
    target_chat = chat_id if chat_id is not None else settings.TELEGRAM_CHAT_ID

    bot_token = bot_token.strip() if bot_token else ""
    target_chat = target_chat.strip() if target_chat else ""

    if not bot_token or not target_chat:
        logger.warning("Telegram Bot Token o Chat ID no configurados. Omitiendo envío de alerta.")
        return {"sent": False, "reason": "telegram_not_configured"}

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": target_chat,
        "text": message_html,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    }

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        resp = await client.post(url, json=payload)
        if resp.status_code == 200:
            data = resp.json()
            message_id = data.get("result", {}).get("message_id")
            return {"sent": True, "message_id": message_id}
        else:
            logger.warning(f"Telegram API devolvió error {resp.status_code}: {resp.text}")
            return {
                "sent": False,
                "reason": f"telegram_api_error_{resp.status_code}",
                "detail": resp.text,
            }
    except Exception as exc:
        logger.error(f"Error de conexión enviando alerta a Telegram: {exc}")
        return {
            "sent": False,
            "reason": "telegram_network_error",
            "detail": str(exc),
        }
    finally:
        if close_client:
            await client.aclose()
