import os
import json
import math
from typing import Dict, Any, List, Optional, Union
import httpx
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class CityConfig:
    def __init__(self, key: str, name: str, lat: float, lon: float, icao: str):
        self.key = key
        self.name = name
        self.lat = lat
        self.lon = lon
        self.icao = icao

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "name": self.name,
            "lat": self.lat,
            "lon": self.lon,
            "icao": self.icao,
        }


PRECONFIGURED_CITIES: Dict[str, CityConfig] = {
    "rio_cuarto": CityConfig(
        key="rio_cuarto",
        name="Río Cuarto",
        lat=-33.1232,
        lon=-64.3493,
        icao="SAOC",
    ),
    "cordoba": CityConfig(
        key="cordoba",
        name="Córdoba Capital",
        lat=-31.4201,
        lon=-64.1888,
        icao="SACO",
    ),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "ClimaRC API"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False

    # WRF JSON location: local file path or HTTP URL
    WRF_JSON_URL: str = os.getenv("WRF_JSON_URL", "data/wrf_latest.json")

    # SMN API Token (Optional)
    SMN_JWT_TOKEN: Optional[str] = os.getenv("SMN_JWT_TOKEN", "")

    # Security & Admin
    CRON_SECRET: str = os.getenv("CRON_SECRET", "climarc-local-secret-cron-key")
    ADMIN_PIN: str = os.getenv("ADMIN_PIN", "")

    # Telegram Bot & Proactive Alerts (Fase 5)
    TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID: str = os.getenv("TELEGRAM_CHAT_ID", "")
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "")

    # Alert Threshold Defaults (leíbles desde entorno para sobrevivir reinicios de Render)
    ALERT_RAIN_PROB_PCT: float = float(os.getenv("ALERT_RAIN_PROB_PCT", "60.0"))
    ALERT_RAIN_MM: float = float(os.getenv("ALERT_RAIN_MM", "1.5"))
    ALERT_WRF_NEARBY_MM: float = float(os.getenv("ALERT_WRF_NEARBY_MM", "8.0"))
    ALERT_GUST_KMH: float = float(os.getenv("ALERT_GUST_KMH", "45.0"))
    ALERT_TEMP_DROP_C: float = float(os.getenv("ALERT_TEMP_DROP_C", "6.0"))
    ALERT_COLD_APPARENT_C: float = float(os.getenv("ALERT_COLD_APPARENT_C", "5.0"))
    ALERT_CAPE_JKG: float = float(os.getenv("ALERT_CAPE_JKG", "1000.0"))
    ALERT_COOLDOWN_HOURS: float = float(os.getenv("ALERT_COOLDOWN_HOURS", "4.0"))
    ALERT_QUIET_ENABLED: bool = os.getenv("ALERT_QUIET_ENABLED", "false").lower() in ("true", "1", "yes")
    ALERT_QUIET_START_HOUR: int = int(os.getenv("ALERT_QUIET_START_HOUR", "23"))
    ALERT_QUIET_END_HOUR: int = int(os.getenv("ALERT_QUIET_END_HOUR", "7"))

    # CORS
    ALLOWED_ORIGINS: Union[List[str], str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    @field_validator("ALLOWED_ORIGINS", mode="after")
    @classmethod
    def parse_allowed_origins(cls, v: Any) -> Any:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        return v

    # Database
    DATABASE_PATH: str = os.getenv("DATABASE_PATH", "data/climarc.db")

    # Timeouts and User-Agent
    USER_AGENT: str = "ClimaRC-PersonalApp/1.0"
    HTTP_TIMEOUT_SECONDS: float = 10.0


settings = Settings()


def get_city_config(city_key: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None) -> CityConfig:
    """
    Returns city configuration by key ('rio_cuarto', 'cordoba')
    or locates the closest preconfigured city if lat/lon are provided.
    Defaults to rio_cuarto.
    """
    if city_key and city_key in PRECONFIGURED_CITIES:
        return PRECONFIGURED_CITIES[city_key]

    if lat is not None and lon is not None:
        rc_dist_km = math.sqrt(((lat - (-33.1232)) * 111.0) ** 2 + ((lon - (-64.3493)) * 93.0) ** 2)
        cba_dist_km = math.sqrt(((lat - (-31.3155)) * 111.0) ** 2 + ((lon - (-64.2133)) * 93.0) ** 2)

        if rc_dist_km < 15.0:
            return CityConfig(
                key="rio_cuarto",
                name="Río Cuarto (GPS)",
                lat=lat,
                lon=lon,
                icao="SAOC",
            )
        elif cba_dist_km < 15.0:
            return CityConfig(
                key="cordoba",
                name="Córdoba (GPS)",
                lat=lat,
                lon=lon,
                icao="SACO",
            )

        best_city = "rio_cuarto" if rc_dist_km < cba_dist_km else "cordoba"
        matched = PRECONFIGURED_CITIES[best_city]
        return CityConfig(
            key=best_city,
            name="Mi ubicación (GPS)",
            lat=lat,
            lon=lon,
            icao=matched.icao,
        )

    return PRECONFIGURED_CITIES["rio_cuarto"]


async def resolve_city_config(
    city_key: Optional[str] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> CityConfig:
    """
    Asynchronously resolves city configuration.
    If GPS coordinates are provided:
    - Within 15 km of Río Cuarto -> 'Río Cuarto (GPS)'
    - Within 15 km of Córdoba -> 'Córdoba (GPS)'
    - Further away -> Queries BigDataCloud reverse geocode client (timeout 2.5s)
      with fallback to 'Mi ubicación (GPS)'.
    """
    if city_key and city_key in PRECONFIGURED_CITIES:
        return PRECONFIGURED_CITIES[city_key]

    if lat is not None and lon is not None:
        rc_dist_km = math.sqrt(((lat - (-33.1232)) * 111.0) ** 2 + ((lon - (-64.3493)) * 93.0) ** 2)
        cba_dist_km = math.sqrt(((lat - (-31.3155)) * 111.0) ** 2 + ((lon - (-64.2133)) * 93.0) ** 2)

        if rc_dist_km < 15.0:
            return CityConfig(
                key="rio_cuarto",
                name="Río Cuarto (GPS)",
                lat=lat,
                lon=lon,
                icao="SAOC",
            )
        elif cba_dist_km < 15.0:
            return CityConfig(
                key="cordoba",
                name="Córdoba (GPS)",
                lat=lat,
                lon=lon,
                icao="SACO",
            )

        closest_key = "rio_cuarto" if rc_dist_km < cba_dist_km else "cordoba"
        closest_icao = "SAOC" if rc_dist_km < cba_dist_km else "SACO"

        # Reverse geocoding lookup
        url = f"https://api.bigdatacloud.net/data/reverse-geocode-client?latitude={lat}&longitude={lon}&localityLanguage=es"
        try:
            close_client = False
            req_client = client
            if req_client is None:
                req_client = httpx.AsyncClient(timeout=2.5)
                close_client = True
            try:
                resp = await req_client.get(url, follow_redirects=True, timeout=2.5)
                if resp.status_code == 200:
                    data = resp.json()
                    locality = data.get("locality") or data.get("city")
                    if locality:
                        if locality.startswith("Departamento "):
                            locality = locality.replace("Departamento ", "")
                        return CityConfig(
                            key=closest_key,
                            name=f"{locality} (GPS)",
                            lat=lat,
                            lon=lon,
                            icao=closest_icao,
                        )
            finally:
                if close_client:
                    await req_client.aclose()
        except Exception:
            pass

        return CityConfig(
            key=closest_key,
            name="Mi ubicación (GPS)",
            lat=lat,
            lon=lon,
            icao=closest_icao,
        )

    return PRECONFIGURED_CITIES["rio_cuarto"]
