import os
import json
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

import joblib
import pandas as pd
import numpy as np

# Rio Cuarto (SAOC) coordinates
SAOC_LAT = -33.1232
SAOC_LON = -64.3493
MAX_DISTANCE_KM = 40.0

TEMP_CLAMP_MIN = -4.5
TEMP_CLAMP_MAX = 4.5
WIND_CLAMP_MIN = -18.0
WIND_CLAMP_MAX = 18.0

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MODELS_DIR = os.path.join(BASE_DIR, "models")
TEMP_MODEL_FILE = os.path.join(MODELS_DIR, "riocuarto_temp_bias.joblib")
WIND_MODEL_FILE = os.path.join(MODELS_DIR, "riocuarto_wind_bias.joblib")
METRICS_FILE = os.path.join(MODELS_DIR, "metrics.json")


def haversine_distance_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculates great-circle distance between two points in kilometers."""
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2.0) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(max(0.0, 1.0 - a)))
    return r * c


class MLBiasCorrector:
    """
    LightGBM local bias corrector for Rio Cuarto (SAOC).
    Loads models and metrics once at startup (singleton).
    Applies 5 mandatory safeguards:
      1. Distance < 40 km from SAOC.
      2. Model improvement validation (temp_improved / wind_improved).
      3. Valid UTC hours (audited coverage >= 40 samples in training).
      4. Preserves Nowcast blending priority at t+0.
      5. Physical clamping (temp [-4.5, 4.5] C, wind [-18.0, 18.0] km/h, final wind >= 0).
    """

    _instance: Optional["MLBiasCorrector"] = None

    def __new__(cls) -> "MLBiasCorrector":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialize()
        return cls._instance

    def _initialize(self):
        self.temp_model = None
        self.wind_model = None
        self.metrics: Dict[str, Any] = {}
        self.valid_utc_hours: List[int] = []
        self.feature_names: List[str] = []
        self.temp_improved = False
        self.wind_improved = False
        self.is_loaded = False

        try:
            if os.path.exists(METRICS_FILE):
                with open(METRICS_FILE, "r", encoding="utf-8") as f:
                    self.metrics = json.load(f)
                self.valid_utc_hours = self.metrics.get("valid_utc_hours", [])
                self.feature_names = self.metrics.get("feature_names", [])
                self.temp_improved = bool(self.metrics.get("temp_improved", False))
                self.wind_improved = bool(self.metrics.get("wind_improved", False))

            if os.path.exists(TEMP_MODEL_FILE):
                self.temp_model = joblib.load(TEMP_MODEL_FILE)

            if os.path.exists(WIND_MODEL_FILE):
                self.wind_model = joblib.load(WIND_MODEL_FILE)

            if self.temp_model is not None and self.wind_model is not None and self.metrics:
                self.is_loaded = True
        except Exception:
            self.is_loaded = False

    def reload(self):
        """Forces reload of models and metrics from disk."""
        self._initialize()

    def get_metadata(self) -> Dict[str, Any]:
        """Returns summary of ML model metrics for API response."""
        if not self.is_loaded or not self.metrics:
            return {
                "available": False,
                "status": "models_not_loaded",
            }
        return {
            "available": True,
            "model_version": self.metrics.get("model_version", "lightgbm_v1"),
            "station": self.metrics.get("station", "SAOC"),
            "city": self.metrics.get("city", "rio_cuarto"),
            "trained_at_utc": self.metrics.get("trained_at_utc"),
            "samples_matched": self.metrics.get("samples_matched", 0),
            "temp_improved": self.temp_improved,
            "wind_improved": self.wind_improved,
            "temperature_metrics": self.metrics.get("temperature", {}),
            "wind_metrics": self.metrics.get("wind", {}),
            "valid_utc_hours_count": len(self.valid_utc_hours),
        }

    def is_location_eligible(
        self,
        city_key: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> bool:
        """Safeguard 1: Distance < 40 km from SAOC."""
        if lat is not None and lon is not None:
            dist = haversine_distance_km(lat, lon, SAOC_LAT, SAOC_LON)
            return dist < MAX_DISTANCE_KM
        if city_key is not None:
            return city_key == "rio_cuarto"
        return False

    def compute_adjustments_for_step(
        self,
        timestamp_utc: str,
        pred_temp_c: float,
        pred_rh_pct: Optional[float],
        pred_pressure_msl: Optional[float],
        pred_precip_mm: Optional[float],
        pred_wind_kmh: float,
        pred_wind_dir_deg: Optional[float],
        temp_trend_3h: float = 0.0,
        city_key: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
    ) -> Tuple[float, float, bool]:
        """
        Computes clamped temperature and wind adjustments for a single hourly step.
        Returns: (ml_temp_adj_c, ml_wind_adj_kmh, ml_applied)
        """
        # Safeguard 1: Location eligibility
        if not self.is_location_eligible(city_key=city_key, lat=lat, lon=lon):
            return 0.0, 0.0, False

        # Safeguard 2: Models loaded and validated
        if not self.is_loaded or (not self.temp_improved and not self.wind_improved):
            return 0.0, 0.0, False

        # Safeguard 3: Valid UTC hour
        try:
            dt = datetime.fromisoformat(timestamp_utc.replace("Z", "+00:00"))
            hour = dt.hour
            doy = dt.timetuple().tm_yday
        except Exception:
            return 0.0, 0.0, False

        if hour not in self.valid_utc_hours:
            return 0.0, 0.0, False

        # Build feature vector
        hour_sin = math.sin(2.0 * math.pi * hour / 24.0)
        hour_cos = math.cos(2.0 * math.pi * hour / 24.0)
        doy_sin = math.sin(2.0 * math.pi * doy / 365.25)
        doy_cos = math.cos(2.0 * math.pi * doy / 365.25)

        w_kmh = float(pred_wind_kmh)
        w_dir = float(pred_wind_dir_deg) if pred_wind_dir_deg is not None else 0.0
        rad = math.radians(w_dir)
        wind_u = -w_kmh * math.sin(rad)
        wind_v = -w_kmh * math.cos(rad)

        feat_dict = {
            "hour_sin": hour_sin,
            "hour_cos": hour_cos,
            "doy_sin": doy_sin,
            "doy_cos": doy_cos,
            "pred_temp_c": float(pred_temp_c),
            "pred_rh_pct": float(pred_rh_pct) if pred_rh_pct is not None else 50.0,
            "pred_pressure_msl": float(pred_pressure_msl) if pred_pressure_msl is not None else 1013.25,
            "pred_precip_mm": float(pred_precip_mm) if pred_precip_mm is not None else 0.0,
            "pred_wind_kmh": w_kmh,
            "wind_u": wind_u,
            "wind_v": wind_v,
            "temp_trend_3h": float(temp_trend_3h),
        }

        # Build single-row DataFrame with exact columns to avoid feature names warning
        df = pd.DataFrame([feat_dict], columns=self.feature_names)

        temp_adj = 0.0
        wind_adj = 0.0

        if self.temp_improved and self.temp_model is not None:
            raw_t_adj = float(self.temp_model.predict(df)[0])
            # Safeguard 5: Clamping
            temp_adj = max(TEMP_CLAMP_MIN, min(TEMP_CLAMP_MAX, raw_t_adj))

        if self.wind_improved and self.wind_model is not None:
            raw_w_adj = float(self.wind_model.predict(df)[0])
            # Safeguard 5: Clamping
            wind_adj = max(WIND_CLAMP_MIN, min(WIND_CLAMP_MAX, raw_w_adj))

        return round(temp_adj, 2), round(wind_adj, 2), True


# Global singleton instance
ml_corrector = MLBiasCorrector()
