from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
import httpx

from .config import settings, get_city_config, PRECONFIGURED_CITIES
from .database import (
    init_db,
    get_cached_payload,
    set_cached_payload,
    get_metar_history,
    calculate_accuracy_metrics,
)
from .services.metar_service import fetch_metar_observations
from .services.open_meteo_service import fetch_open_meteo_forecast
from .services.wrf_reader_service import read_wrf_forecast
from .services.smn_alerts_service import fetch_smn_alerts
from .services.local_alerts_detector import detect_local_alerts
from .services.ensemble_engine import generate_ensemble_forecast

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
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
