import os
from typing import Dict, Any, List, Optional
from pydantic import Field
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

    # Security
    CRON_SECRET: str = os.getenv("CRON_SECRET", "climarc-local-secret-cron-key")

    # CORS
    ALLOWED_ORIGINS: List[str] = ["*"]

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
        # Calculate squared distance to known cities
        best_city = "rio_cuarto"
        best_dist = float("inf")
        for key, c in PRECONFIGURED_CITIES.items():
            dist = (c.lat - lat) ** 2 + (c.lon - lon) ** 2
            if dist < best_dist:
                best_dist = dist
                best_city = key
        # Return city config matching target coordinates with closest airport
        matched = PRECONFIGURED_CITIES[best_city]
        return CityConfig(
            key=best_city,
            name=f"Ubicación ({lat:.3f}, {lon:.3f})",
            lat=lat,
            lon=lon,
            icao=matched.icao,
        )

    return PRECONFIGURED_CITIES["rio_cuarto"]
