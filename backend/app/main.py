import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Query, HTTPException, Request, Header, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx

from .config import settings, get_city_config, resolve_city_config, PRECONFIGURED_CITIES
from .database import (
    init_db,
    get_cached_payload,
    set_cached_payload,
    get_metar_history,
    calculate_accuracy_metrics,
    get_alert_settings,
    update_alert_settings,
    get_recent_alerts,
    check_db_health,
    prune_old_records,
)
from .services.metar_service import fetch_metar_observations
from .services.open_meteo_service import fetch_open_meteo_forecast
from .services.wrf_reader_service import read_wrf_forecast
from .services.smn_alerts_service import fetch_smn_alerts
from .services.local_alerts_detector import detect_local_alerts
from .services.ensemble_engine import generate_ensemble_forecast, generate_executive_summary
from .services.sentinel_service import get_sentinels_summary, detect_approaching_front
from .services.hail_service import evaluate_hail_risk
from .services.route_service import get_route_weather
from .services.telegram_service import format_test_message, send_telegram_alert
from .services.alerts_service import check_and_dispatch_cron, generate_daily_briefing_html
from .services.telegram_bot_handler import (
    handle_telegram_message_background,
    is_update_processed,
    mark_update_processed,
)

RAINVIEWER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"
REALEARTH_TIMES_URL = "https://realearth.ssec.wisc.edu/api/times?products=G19-ABI-FD-BAND13-GRAD"
REALEARTH_GLM_TIMES_URL = "https://realearth.ssec.wisc.edu/api/times?products=glmgroupdensity"


async def periodic_alerts_checker():
    """Background task inside FastAPI that checks and dispatches alerts every 15 minutes when container is awake."""
    await asyncio.sleep(30)
    while True:
        try:
            logger.info("Ejecutando chequeo periódico interno de alertas...")
            await check_and_dispatch_cron(city_key="rio_cuarto")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.warning(f"Error en chequeo periódico interno de alertas: {e}")
        await asyncio.sleep(900)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite schema
    init_db()
    task = asyncio.create_task(periodic_alerts_checker())
    try:
        yield
    finally:
        task.cancel()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS configuration
extra_cors_origins = [settings.FRONTEND_URL.rstrip("/")] if settings.FRONTEND_URL else []
allowed_origins = list(set(settings.ALLOWED_ORIGINS + extra_cors_origins + [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/ping")
async def ping():
    """Instant alive check. Zero DB or network overhead."""
    return {"status": "ok"}


@app.get("/api/health")
async def health():
    """Comprehensive health check across data sources."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Fast check of WRF status
    wrf_info = await read_wrf_forecast(force_refresh=False)
    # Check METAR history in DB
    rc_history = get_metar_history("SAOC", hours=24)
    cba_history = get_metar_history("SACO", hours=24)

    return {
        "status": "healthy",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "timestamp_utc": now_utc,
        "sources": {
            "wrf": {
                "available": wrf_info.get("wrf_available", False),
                "run_time_utc": wrf_info.get("run_time_utc"),
                "age_hours": wrf_info.get("age_hours"),
                "reason": wrf_info.get("reason"),
            },
            "metar": {
                "saoc_records_cached": len(rc_history),
                "saco_records_cached": len(cba_history),
                "latest_saoc_utc": rc_history[0]["obs_time_utc"] if rc_history else None,
                "latest_saco_utc": cba_history[0]["obs_time_utc"] if cba_history else None,
            },
            "smn_official": {
                "jwt_configured": bool(settings.SMN_JWT_TOKEN and settings.SMN_JWT_TOKEN.strip()),
                "status": "graceful_degradation",
            },
            "database": check_db_health(),
        },
    }


@app.get("/api/weather")
async def get_weather(
    city: Optional[str] = Query(None, description="City key: rio_cuarto or cordoba"),
    lat: Optional[float] = Query(None, description="Arbitrary latitude in Cordoba province"),
    lon: Optional[float] = Query(None, description="Arbitrary longitude in Cordoba province"),
    refresh: bool = Query(False, description="Force refresh bypass cache"),
):
    """
    Main unified forecast endpoint.
    - Resolves location (Río Cuarto, Córdoba Capital or custom lat/lon).
    - Checks SQLite cache (TTL 15 min).
    - Asynchronously queries METAR, Open-Meteo, WRF, and SMN alerts.
    - Fuses forecasts with Nowcast blending and Confidence Index.
    - Evaluates local severe weather alerts.
    """
    city_cfg = await resolve_city_config(city_key=city, lat=lat, lon=lon)

    cache_key = f"weather:{city_cfg.key}" if (lat is None or lon is None) else f"weather:{lat:.4f}:{lon:.4f}"

    if not refresh:
        cached = get_cached_payload(cache_key, max_age_seconds=300)
        if cached:
            return cached

    # Fetch live sources
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
        # Metar for all 4 sentinel stations in single HTTP call (SAOC, SACO, SAOR, SAOL)
        metar_map = await fetch_metar_observations(client=client, force_refresh=refresh)
        metar_obs = metar_map.get(city_cfg.icao)

        # Open-Meteo multi-model (including AIFS, 500hPa shear, freezing level, UV & solar times)
        open_meteo_res = await fetch_open_meteo_forecast(lat=city_cfg.lat, lon=city_cfg.lon, client=client)

        # WRF cached or local
        wrf_res = await read_wrf_forecast(force_refresh=refresh, client=client)

        # SMN official alerts (graceful)
        smn_alerts_res = await fetch_smn_alerts(client=client)

        # Two-stage hail evaluation (Atmospheric potential + GOES-19 live core scan)
        hail_risk = await evaluate_hail_risk(open_meteo_res, wrf_res, metar_map, client=client)

    # Sentinel stations summary and front detection
    sentinels = get_sentinels_summary(metar_map)
    sentinel_front = detect_approaching_front(metar_map)

    # Metar history for pressure trend
    metar_history = get_metar_history(city_cfg.icao, hours=6)

    # Local severe alerts detector
    local_alerts = detect_local_alerts(
        city_key=city_cfg.key,
        metar_data=metar_obs,
        metar_history_records=metar_history,
        open_meteo_hourly=open_meteo_res.get("hourly", []),
        wrf_city_data=wrf_res.get("cities", {}).get(city_cfg.key),
    )

    # Combine alerts: SMN alerts + Local alerts
    all_alerts = list(smn_alerts_res.get("alerts", [])) + local_alerts

    center_bt = None
    if hail_risk:
        center_bt = hail_risk.get("center_brightness_temp_c")
        if center_bt is None and "stage2_details" in hail_risk:
            center_bt = hail_risk["stage2_details"].get("center_brightness_temp_c")

    # Generate Ensemble Forecast
    ensemble = generate_ensemble_forecast(
        city_key=city_cfg.key,
        open_meteo_data=open_meteo_res,
        wrf_data=wrf_res,
        metar_data=metar_obs,
        max_hourly_steps=72,
        lat=city_cfg.lat,
        lon=city_cfg.lon,
        metar_history_records=metar_history,
        center_brightness_temp_c=center_bt if city_cfg.key == "rio_cuarto" else None,
    )

    # Executive 1-liner summary
    summary_one_liner = generate_executive_summary(
        current=ensemble["current"],
        hourly=ensemble["hourly"],
        daily=ensemble["daily"],
        hail_risk=hail_risk,
        sentinel_front=sentinel_front,
    )

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "city": city_cfg.to_dict(),
        "updated_at_utc": now_utc,
        "summary_one_liner": summary_one_liner,
        "current": ensemble["current"],
        "hourly": ensemble["hourly"],
        "daily": ensemble["daily"],
        "sentinels": sentinels,
        "sentinel_front": sentinel_front,
        "hail_risk": hail_risk,
        "alerts": all_alerts,
        "models_active": ensemble["models_active"],
        "confidence": ensemble["overall_confidence"],
        "wrf_available": ensemble["wrf_available"],
        "smn_official_available": smn_alerts_res.get("smn_available", False),
        "ml_metadata": ensemble.get("ml_metadata", {}),
    }

    # Save to SQLite cache
    set_cached_payload(cache_key, payload)

    return payload


@app.get("/api/radar")
async def get_radar():
    """
    Cached proxy (5 min TTL) for RainViewer real-time radar metadata and
    SSEC RealEarth GOES-19 Full Disk Clean Infrared (Band 13) satellite imagery.
    Reduces client calls to external API while ensuring map tile availability.
    Gracefully degrades if one provider is unreachable.
    """
    cache_key = "radar:rainviewer_maps"
    cached = get_cached_payload(cache_key, max_age_seconds=300)
    if cached:
        return cached

    headers = {"User-Agent": settings.USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
            rv_task = client.get(RAINVIEWER_MAPS_URL, headers=headers)
            re_task = client.get(REALEARTH_TIMES_URL, headers=headers)
            glm_task = client.get(REALEARTH_GLM_TIMES_URL, headers=headers)
            rv_res, re_res, glm_res = await asyncio.gather(rv_task, re_task, glm_task, return_exceptions=True)

            if isinstance(rv_res, httpx.Response) and rv_res.status_code == 200:
                data = rv_res.json()
            elif isinstance(rv_res, httpx.Response):
                raise HTTPException(status_code=rv_res.status_code, detail="RainViewer returned non-200")
            elif isinstance(rv_res, Exception):
                raise rv_res
            else:
                raise HTTPException(status_code=502, detail="No response from RainViewer")

            # Parse GOES-19 Infrared satellite frames from SSEC RealEarth (enhanced gradient)
            sat_frames = []
            if isinstance(re_res, httpx.Response) and re_res.status_code == 200:
                try:
                    re_json = re_res.json()
                    re_times = re_json.get("G19-ABI-FD-BAND13-GRAD", []) or re_json.get("G19-ABI-FD-BAND13", [])
                    # Take up to last 12 frames (spanning ~2 hours of real satellite imagery)
                    selected_times = re_times[-12:] if len(re_times) >= 12 else re_times
                    for t_str in selected_times:
                        try:
                            dt = datetime.strptime(t_str, "%Y%m%d.%H%M%S").replace(tzinfo=timezone.utc)
                            u_ts = int(dt.timestamp())
                        except Exception:
                            u_ts = int(time.time())
                        sat_frames.append({
                            "time": u_ts,
                            "time_str": t_str,
                            "path": f"/tiles/G19-ABI-FD-BAND13-GRAD/{t_str}",
                        })
                except Exception:
                    pass

            # Parse GLM lightning activity frames from SSEC RealEarth
            if isinstance(glm_res, httpx.Response) and glm_res.status_code == 200:
                try:
                    glm_json = glm_res.json()
                    glm_times = glm_json.get("glmgroupdensity", [])
                    selected_glm = glm_times[-12:] if len(glm_times) >= 12 else glm_times
                    glm_frames = []
                    for t_str in selected_glm:
                        try:
                            dt = datetime.strptime(t_str, "%Y%m%d.%H%M%S").replace(tzinfo=timezone.utc)
                            u_ts = int(dt.timestamp())
                        except Exception:
                            u_ts = int(time.time())
                        glm_frames.append({
                            "time": u_ts,
                            "time_str": t_str,
                            "path": f"/tiles/glmgroupdensity/{t_str}",
                        })
                    if glm_frames:
                        data["lightning"] = {
                            "provider": "realearth_glm",
                            "product": "glmgroupdensity",
                            "name": "Actividad de Rayos GLM (GOES-East)",
                            "tile_url_template": "https://realearth.ssec.wisc.edu/tiles/glmgroupdensity/{z}/{x}/{y}.png?time={time}",
                            "max_native_zoom": 7,
                            "latest_time": glm_times[-1] if glm_times else None,
                            "frames": glm_frames,
                        }
                except Exception:
                    pass

            # Populate satellite block
            rv_existing_sat = data.get("satellite", {}).get("infrared", [])
            if not rv_existing_sat and sat_frames:
                data["satellite"] = {
                    "provider": "realearth_goes19",
                    "product": "G19-ABI-FD-BAND13-GRAD",
                    "name": "GOES-19 Band 13 (IR Gradiente)",
                    "tile_url_template": "https://realearth.ssec.wisc.edu/tiles/G19-ABI-FD-BAND13-GRAD/{z}/{x}/{y}.png?time={time}",
                    "max_native_zoom": 7,
                    "infrared": sat_frames,
                }
            elif rv_existing_sat:
                data["satellite"] = {
                    "provider": "rainviewer",
                    "product": "infrared",
                    "name": "RainViewer Satellite IR",
                    "tile_url_template": f"{data.get('host', '')}{{path}}/256/{{z}}/{{x}}/{{y}}/0/0_0.png",
                    "max_native_zoom": 7,
                    "infrared": rv_existing_sat,
                }
            else:
                data["satellite"] = {
                    "provider": "unavailable",
                    "infrared": [],
                }

            set_cached_payload(cache_key, data)
            return data
    except Exception as exc:
        # Fallback to stale cache if present
        stale = get_cached_payload(cache_key, max_age_seconds=86400)
        if stale:
            return stale
        raise HTTPException(status_code=503, detail=f"No se pudo consultar el radar de RainViewer: {exc}")


@app.get("/api/route-weather")
async def route_weather(
    origin: str = Query("rio_cuarto", description="Corridor origin (preset or city key)"),
    destination: str = Query("cordoba", description="Corridor destination preset (cordoba, villa_maria, villa_mercedes, santa_rosa_calamuchita, san_luis)"),
    dest_lat: Optional[float] = Query(None, description="Optional custom destination latitude"),
    dest_lon: Optional[float] = Query(None, description="Optional custom destination longitude"),
):
    """
    Evaluates 3 strategic points along transport routes from Río Cuarto
    (Origin, Midpoint, Destination) to determine road safety, rain risk, crosswinds and visibility.
    """
    return await get_route_weather(
        origin=origin,
        destination=destination,
        dest_lat=dest_lat,
        dest_lon=dest_lon,
    )


@app.get("/api/accuracy")
async def get_accuracy(
    city: Optional[str] = Query("rio_cuarto", description="City key: rio_cuarto or cordoba")
):
    """
    Precision auditor: crosses historical forecast snapshots with real airport METAR readings
    to compute Mean Absolute Error (MAE) for temperature (°C) and wind speed (km/h).
    """
    city_cfg = get_city_config(city_key=city)
    metrics = calculate_accuracy_metrics(city=city_cfg.key, icao=city_cfg.icao)
    return metrics


# -------------------------------------------------------------
# FASE 5: MOTOR DE ALERTAS PROACTIVAS VÍA TELEGRAM BOT
# -------------------------------------------------------------

logger = logging.getLogger(__name__)


def verify_cron_secret(x_cron_secret: Optional[str] = Header(None, alias="X-CRON-SECRET")) -> bool:
    """
    Verifies that the incoming request contains the secret cron authorization header.
    Returns 401 if missing or mismatched.
    """
    expected = settings.CRON_SECRET.strip() if settings.CRON_SECRET else ""
    if not expected:
        raise HTTPException(
            status_code=401,
            detail="CRON_SECRET no está configurado en el servidor.",
        )
    if not x_cron_secret or x_cron_secret.strip() != expected:
        raise HTTPException(
            status_code=401,
            detail="Header X-CRON-SECRET inválido o ausente.",
        )
    return True


def verify_telegram_webhook_secret(
    x_telegram_secret: Optional[str] = Header(None, alias="X-Telegram-Bot-Api-Secret-Token")
) -> bool:
    """
    Validates X-Telegram-Bot-Api-Secret-Token against TELEGRAM_WEBHOOK_SECRET.
    Returns 401 if missing or mismatched.
    """
    expected = settings.TELEGRAM_WEBHOOK_SECRET.strip() if settings.TELEGRAM_WEBHOOK_SECRET else ""
    if not expected or not x_telegram_secret or x_telegram_secret.strip() != expected:
        raise HTTPException(
            status_code=401,
            detail="Header X-Telegram-Bot-Api-Secret-Token inválido o ausente.",
        )
    return True


def verify_admin_pin(x_admin_pin: Optional[str] = Header(None, alias="X-ADMIN-PIN")) -> bool:
    """
    Verifies the admin PIN for mutating settings or triggering tests.
    If ADMIN_PIN is empty in local development, allows with a warning log.
    If ADMIN_PIN is defined, enforces exact match or raises 401.
    """
    expected = settings.ADMIN_PIN.strip() if settings.ADMIN_PIN else ""
    if not expected:
        logger.warning("ADMIN_PIN no configurado en entorno. Permitido en desarrollo local con advertencia.")
        return True
    if not x_admin_pin or x_admin_pin.strip() != expected:
        raise HTTPException(
            status_code=401,
            detail="PIN de administrador inválido o no proporcionado (X-ADMIN-PIN).",
        )
    return True


class AlertSettingsUpdate(BaseModel):
    rain_prob_pct: Optional[float] = Field(None, ge=0.0, le=100.0)
    rain_mm: Optional[float] = Field(None, ge=0.0)
    wrf_nearby_mm: Optional[float] = Field(None, ge=0.0)
    gust_kmh: Optional[float] = Field(None, ge=0.0)
    temp_drop_c: Optional[float] = Field(None, ge=0.0)
    cold_apparent_c: Optional[float] = None
    cape_jkg: Optional[float] = Field(None, ge=0.0)
    cooldown_hours: Optional[float] = Field(None, ge=0.1, le=72.0)
    quiet_enabled: Optional[bool] = None
    quiet_start_hour: Optional[int] = Field(None, ge=0, le=23)
    quiet_end_hour: Optional[int] = Field(None, ge=0, le=23)
    briefing_enabled: Optional[bool] = None
    briefing_send_hour: Optional[int] = Field(None, ge=0, le=23)
    briefing_window_start: Optional[int] = Field(None, ge=0, le=23)
    briefing_window_end: Optional[int] = Field(None, ge=0, le=23)


@app.post("/api/telegram/webhook", dependencies=[Depends(verify_telegram_webhook_secret)])
async def telegram_webhook_endpoint(
    request: Request,
    background_tasks: BackgroundTasks,
):
    """
    Interactive Telegram Webhook entrypoint.
    - Validates X-Telegram-Bot-Api-Secret-Token against TELEGRAM_WEBHOOK_SECRET.
    - Strictly whitelists chat.id against TELEGRAM_CHAT_ID (silent 200 OK for untrusted chats).
    - Deduplicates updates by update_id to protect against Render cold-start retries.
    - Enqueues command handling into FastAPI BackgroundTasks and returns 200 OK immediately (<50ms).
    """
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    update_id = payload.get("update_id")
    if update_id is not None:
        if is_update_processed(update_id):
            logger.debug(f"Telegram update_id {update_id} ya procesado. Descartando reintento.")
            return {"ok": True, "status": "duplicate"}
        mark_update_processed(update_id)

    message = payload.get("message") or payload.get("edited_message")
    if not message:
        return {"ok": True, "status": "no_message"}

    chat = message.get("chat", {})
    chat_id = str(chat.get("id", "")).strip()
    allowed_chat_id = str(settings.TELEGRAM_CHAT_ID).strip() if settings.TELEGRAM_CHAT_ID else ""

    if not allowed_chat_id or chat_id != allowed_chat_id:
        logger.warning(
            f"Update de chat_id={chat_id} ignorado silenciosamente por whitelist (esperado={allowed_chat_id})."
        )
        return {"ok": True, "status": "ignored"}

    background_tasks.add_task(handle_telegram_message_background, message)
    return {"ok": True}


@app.post("/api/alerts/check", dependencies=[Depends(verify_cron_secret)])
async def check_alerts_cron_endpoint(
    city: Optional[str] = Query("rio_cuarto", description="City key: rio_cuarto")
):
    """
    Cron trigger for proactive weather alerts evaluation.
    Evaluates Río Cuarto conditions and dispatches to Telegram according to cooldown and quiet rules.
    Protected strictly by X-CRON-SECRET.
    """
    try:
        prune_res = prune_old_records(days=90)
        logger.info(f"Retención automática de DB ejecutada: {prune_res}")
    except Exception as exc:
        logger.warning(f"Error en retención automática de DB: {exc}")

    result = await check_and_dispatch_cron(city_key=city or "rio_cuarto")
    return result


@app.get("/api/alerts/settings")
async def get_alerts_settings_endpoint():
    """
    Returns current alert thresholds, quiet hours, recent alerts sent,
    and a boolean telegram_configured.
    NEVER exposes TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID.
    """
    current_settings = get_alert_settings()
    recent = get_recent_alerts(limit=10)
    telegram_is_configured = bool(
        settings.TELEGRAM_BOT_TOKEN
        and settings.TELEGRAM_BOT_TOKEN.strip()
        and settings.TELEGRAM_CHAT_ID
        and settings.TELEGRAM_CHAT_ID.strip()
    )
    return {
        "settings": current_settings,
        "telegram_configured": telegram_is_configured,
        "recent_alerts": recent,
    }


@app.put("/api/alerts/settings", dependencies=[Depends(verify_admin_pin)])
async def update_alerts_settings_endpoint(payload: AlertSettingsUpdate):
    """
    Updates alert thresholds and quiet hours in SQLite.
    Protected strictly by X-ADMIN-PIN header.
    """
    updates = payload.model_dump(exclude_unset=True)
    updated = update_alert_settings(updates)
    return {
        "status": "ok",
        "settings": updated,
        "message": "Ajustes de alertas actualizados exitosamente.",
    }


@app.post("/api/alerts/test", dependencies=[Depends(verify_admin_pin)])
async def test_alerts_endpoint():
    """
    Sends a real test alert to Telegram with current Río Cuarto readings.
    Protected strictly by X-ADMIN-PIN header.
    """
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
        metar_map = await fetch_metar_observations(station_ids=["SAOC"], client=client)
        metar_obs = metar_map.get("SAOC")

    current_summary = None
    if metar_obs:
        current_summary = {
            "temperature_c": metar_obs.get("temp_c"),
            "wind_speed_kmh": metar_obs.get("wind_speed_kmh"),
            "wind_gust_kmh": metar_obs.get("wind_gust_kmh"),
            "pressure_hpa": metar_obs.get("qnh_hpa"),
        }

    test_msg = format_test_message(
        city_name="Río Cuarto",
        station_icao="SAOC",
        current_weather=current_summary,
    )
    send_res = await send_telegram_alert(test_msg)
    return {
        "status": "ok" if send_res.get("sent") else "warning",
        "result": send_res,
    }


@app.post("/api/alerts/briefing/test", dependencies=[Depends(verify_admin_pin)])
async def test_briefing_endpoint():
    """
    Sends a test Daily Briefing to Telegram with current conditions and overnight forecast.
    Protected strictly by X-ADMIN-PIN header.
    """
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
        alert_settings = get_alert_settings()
        briefing_html = await generate_daily_briefing_html(
            city_key="rio_cuarto",
            alert_settings=alert_settings,
            client=client,
        )
        send_res = await send_telegram_alert(message_html=briefing_html, client=client)
    return {
        "status": "ok" if send_res.get("sent") else "warning",
        "result": send_res,
    }
