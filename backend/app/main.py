import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from fastapi import FastAPI, Query, HTTPException, Request, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
import httpx

from .config import settings, get_city_config, PRECONFIGURED_CITIES
from .database import (
    init_db,
    get_cached_payload,
    set_cached_payload,
    get_metar_history,
    calculate_accuracy_metrics,
    get_alert_settings,
    update_alert_settings,
    get_recent_alerts,
)
from .services.metar_service import fetch_metar_observations
from .services.open_meteo_service import fetch_open_meteo_forecast
from .services.wrf_reader_service import read_wrf_forecast
from .services.smn_alerts_service import fetch_smn_alerts
from .services.local_alerts_detector import detect_local_alerts
from .services.ensemble_engine import generate_ensemble_forecast
from .services.telegram_service import format_test_message, send_telegram_alert
from .services.alerts_service import check_and_dispatch_cron

RAINVIEWER_MAPS_URL = "https://api.rainviewer.com/public/weather-maps.json"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize SQLite schema
    init_db()
    yield


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    lifespan=lifespan,
)

# CORS configuration
allowed_origins = list(set(settings.ALLOWED_ORIGINS + [
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
            "database": {
                "status": "connected",
                "path": settings.DATABASE_PATH,
            },
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
    city_cfg = get_city_config(city_key=city, lat=lat, lon=lon)

    cache_key = f"weather:{city_cfg.key}" if (lat is None or lon is None) else f"weather:{lat:.4f}:{lon:.4f}"

    if not refresh:
        cached = get_cached_payload(cache_key, max_age_seconds=900)
        if cached:
            return cached

    # Fetch live sources
    async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
        # Metar for city airport
        metar_map = await fetch_metar_observations(station_ids=[city_cfg.icao], client=client)
        metar_obs = metar_map.get(city_cfg.icao)

        # Open-Meteo multi-model
        open_meteo_res = await fetch_open_meteo_forecast(lat=city_cfg.lat, lon=city_cfg.lon, client=client)

        # WRF cached or local
        wrf_res = await read_wrf_forecast(force_refresh=refresh, client=client)

        # SMN official alerts (graceful)
        smn_alerts_res = await fetch_smn_alerts(client=client)

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

    # Generate Ensemble Forecast
    ensemble = generate_ensemble_forecast(
        city_key=city_cfg.key,
        open_meteo_data=open_meteo_res,
        wrf_data=wrf_res,
        metar_data=metar_obs,
        max_hourly_steps=72,
        lat=city_cfg.lat,
        lon=city_cfg.lon,
    )

    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    payload = {
        "city": city_cfg.to_dict(),
        "updated_at_utc": now_utc,
        "current": ensemble["current"],
        "hourly": ensemble["hourly"],
        "daily": ensemble["daily"],
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
    Cached proxy (5 min TTL) for RainViewer real-time radar metadata.
    Reduces client calls to external API while ensuring map tile availability.
    """
    cache_key = "radar:rainviewer_maps"
    cached = get_cached_payload(cache_key, max_age_seconds=300)
    if cached:
        return cached

    headers = {"User-Agent": settings.USER_AGENT}
    try:
        async with httpx.AsyncClient(timeout=settings.HTTP_TIMEOUT_SECONDS) as client:
            resp = await client.get(RAINVIEWER_MAPS_URL, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                set_cached_payload(cache_key, data)
                return data
            else:
                raise HTTPException(status_code=resp.status_code, detail="RainViewer returned non-200")
    except Exception as exc:
        # Fallback to stale cache if present
        stale = get_cached_payload(cache_key, max_age_seconds=86400)
        if stale:
            return stale
        raise HTTPException(status_code=503, detail=f"No se pudo consultar el radar de RainViewer: {exc}")


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


@app.post("/api/alerts/check", dependencies=[Depends(verify_cron_secret)])
async def check_alerts_cron_endpoint(
    city: Optional[str] = Query("rio_cuarto", description="City key: rio_cuarto")
):
    """
    Cron trigger for proactive weather alerts evaluation.
    Evaluates Río Cuarto conditions and dispatches to Telegram according to cooldown and quiet rules.
    Protected strictly by X-CRON-SECRET.
    """
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
