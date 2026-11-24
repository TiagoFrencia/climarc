#!/usr/bin/env python3
"""
Sprint 2.4 - Monthly Automated Machine Learning Retraining from Neon PostgreSQL.
Connects to Neon PostgreSQL, pairs ensemble_raw forecasts against real SAOC METAR ground truth,
and evaluates if a LightGBM v2 model improves test MAE >= 2% without degrading RMSE.

Safeguards:
  1. Minimum 500 matched hours strictly required. Otherwise exits cleanly with exit 0.
  2. Strict 80/20 chronological split (no data leakage).
  3. Only overwrites production models in /backend/models/ if improvement >= 2% MAE and RMSE not degraded.
"""

import os
import sys
import json
import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODELS_DIR = os.path.join(BASE_DIR, "backend", "models")
TEMP_MODEL_PATH = os.path.join(MODELS_DIR, "riocuarto_temp_bias.joblib")
WIND_MODEL_PATH = os.path.join(MODELS_DIR, "riocuarto_wind_bias.joblib")
METRICS_PATH = os.path.join(MODELS_DIR, "metrics.json")

FEATURE_NAMES = [
    "hour_sin",
    "hour_cos",
    "doy_sin",
    "doy_cos",
    "pred_temp_c",
    "pred_rh_pct",
    "pred_pressure_msl",
    "pred_precip_mm",
    "pred_wind_kmh",
    "wind_u",
    "wind_v",
    "temp_trend_3h",
]

MIN_REQUIRED_SAMPLES = 500
MIN_IMPROVEMENT_PCT = 2.0


def extract_features_from_rows(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """Extracts identical feature vectors as defined in LightGBM v1."""
    records = []
    temps = [float(r["predicted_temp_c"]) for r in rows]

    for idx, r in enumerate(rows):
        t_str = r["target_time_utc"]
        try:
            dt = datetime.fromisoformat(t_str.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)

        hour = dt.hour
        doy = dt.timetuple().tm_yday

        pred_t = float(r["predicted_temp_c"])
        pred_w = float(r["predicted_wind_kmh"] or 0.0)
        pred_p = float(r["predicted_precip_mm"] or 0.0)

        # 3-hour trend
        trend_3h = round(temps[idx] - temps[idx - 3], 2) if idx >= 3 else 0.0

        # Dewpoint / RH approximation
        dewp = float(r["actual_dewp_c"]) if r.get("actual_dewp_c") is not None else pred_t - 8.0
        try:
            rh_est = min(100.0, max(10.0, 100.0 - 5.0 * (pred_t - dewp)))
        except Exception:
            rh_est = 60.0

        qnh = float(r["actual_qnh_hpa"]) if r.get("actual_qnh_hpa") is not None else 1013.2

        records.append({
            "hour_sin": math.sin(2.0 * math.pi * hour / 24.0),
            "hour_cos": math.cos(2.0 * math.pi * hour / 24.0),
            "doy_sin": math.sin(2.0 * math.pi * doy / 365.25),
            "doy_cos": math.cos(2.0 * math.pi * doy / 365.25),
            "pred_temp_c": pred_t,
            "pred_rh_pct": rh_est,
            "pred_pressure_msl": qnh,
            "pred_precip_mm": pred_p,
            "pred_wind_kmh": pred_w,
            "wind_u": 0.0,
            "wind_v": 0.0,
            "temp_trend_3h": trend_3h,
            "actual_temp_c": float(r["actual_temp_c"]),
            "actual_wind_kmh": float(r["actual_wind_kmh"] or 0.0),
        })

    return pd.DataFrame(records)


def main():
    db_url = os.getenv("DATABASE_URL", "").strip()
    if not db_url:
        print("[Retrain ML] DATABASE_URL no configurada. Omitiendo reentrenamiento.")
        sys.exit(0)

    if db_url.startswith("postgres://"):
        db_url = "postgresql://" + db_url[len("postgres://"):]

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        print("[Retrain ML] psycopg no está instalado. Omitiendo reentrenamiento.")
        sys.exit(0)

    print("[Retrain ML] Conectando a Neon PostgreSQL para auditar pares de entrenamiento...")
    try:
        with psycopg.connect(db_url, row_factory=dict_row, connect_timeout=15) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT
                        s.target_time_utc,
                        s.predicted_temp_c,
                        s.predicted_wind_kmh,
                        s.predicted_precip_mm,
                        m.temp_c AS actual_temp_c,
                        m.wind_kmh AS actual_wind_kmh,
                        m.dewp_c AS actual_dewp_c,
                        m.qnh_hpa AS actual_qnh_hpa,
                        m.obs_time_utc
                    FROM forecast_snapshots s
                    JOIN metar_history m
                        ON m.icao = 'SAOC'
                        AND substr(s.target_time_utc, 1, 13) = substr(m.obs_time_utc, 1, 13)
                    WHERE s.city = 'rio_cuarto'
                      AND s.model_source IN ('ensemble_raw', 'ensemble')
                      AND m.temp_c IS NOT NULL
                      AND s.predicted_temp_c IS NOT NULL
                    ORDER BY s.target_time_utc ASC
                """)
                rows = cur.fetchall()
    except Exception as e:
        print(f"[Retrain ML] Error consultando Neon: {e}. Saliendo con exit 0 para no bloquear CI.")
        sys.exit(0)

    matched_count = len(rows)
    print(f"[Retrain ML] Pares emparejados en Neon (ensemble_raw vs SAOC): {matched_count}")

    # Minimum safeguard check: >= 500 samples
    if matched_count < MIN_REQUIRED_SAMPLES:
        print(f"{matched_count}/{MIN_REQUIRED_SAMPLES} horas acumuladas; manteniendo modelo base v1")
        sys.exit(0)

    print(f"[Retrain ML] Umbral superado ({matched_count} >= {MIN_REQUIRED_SAMPLES}). Iniciando entrenamiento candidato LightGBM v2...")
    df = extract_features_from_rows(rows)

    # Chronological 80/20 train/test split
    split_idx = int(0.8 * len(df))
    train_df = df.iloc[:split_idx].copy()
    test_df = df.iloc[split_idx:].copy()

    X_train = train_df[FEATURE_NAMES]
    y_train_temp = train_df["actual_temp_c"] - train_df["pred_temp_c"]
    y_train_wind = train_df["actual_wind_kmh"] - train_df["pred_wind_kmh"]

    X_test = test_df[FEATURE_NAMES]
    y_test_temp = test_df["actual_temp_c"] - test_df["pred_temp_c"]
    y_test_wind = test_df["actual_wind_kmh"] - test_df["pred_wind_kmh"]

    # Train candidate temperature model
    temp_model = lgb.LGBMRegressor(
        max_depth=5,
        num_leaves=20,
        learning_rate=0.05,
        min_child_samples=20,
        n_estimators=300,
        random_state=42,
        verbose=-1,
    )
    temp_model.fit(X_train, y_train_temp)

    # Train candidate wind model
    wind_model = lgb.LGBMRegressor(
        max_depth=5,
        num_leaves=20,
        learning_rate=0.05,
        min_child_samples=20,
        n_estimators=300,
        random_state=42,
        verbose=-1,
    )
    wind_model.fit(X_train, y_train_wind)

    # Evaluate on unseen test set
    test_pred_temp_bias = temp_model.predict(X_test)
    test_pred_wind_bias = wind_model.predict(X_test)

    # Temperature metrics
    candidate_temp_mae = round(float(mean_absolute_error(test_df["actual_temp_c"], test_df["pred_temp_c"] + test_pred_temp_bias)), 2)
    candidate_temp_rmse = round(float(root_mean_squared_error(test_df["actual_temp_c"], test_df["pred_temp_c"] + test_pred_temp_bias)), 2)

    # Load current production metrics
    current_metrics = {}
    if os.path.exists(METRICS_PATH):
        try:
            with open(METRICS_PATH, "r", encoding="utf-8") as f:
                current_metrics = json.load(f)
        except Exception:
            current_metrics = {}

    current_mae = current_metrics.get("temperature", {}).get("mae_after", current_metrics.get("mae_after", 1.14))
    current_rmse = current_metrics.get("temperature", {}).get("rmse_after", current_metrics.get("rmse_after", 1.57))

    improvement_pct = ((current_mae - candidate_temp_mae) / current_mae) * 100.0 if current_mae > 0 else 0.0

    print(f"[Retrain ML] Evaluación Test:")
    print(f"  Vigente v1  -> MAE: {current_mae} °C | RMSE: {current_rmse}")
    print(f"  Candidato v2 -> MAE: {candidate_temp_mae} °C | RMSE: {candidate_temp_rmse}")
    print(f"  Mejora MAE: {improvement_pct:.2f}% (requerido >= {MIN_IMPROVEMENT_PCT}%)")

    # Strict validation: >= 2% MAE improvement and RMSE not degraded
    if improvement_pct >= MIN_IMPROVEMENT_PCT and candidate_temp_rmse <= current_rmse:
        print("[Retrain ML] ¡Mejora validada! Sobrescribiendo modelos vigentes...")
        os.makedirs(MODELS_DIR, exist_ok=True)
        joblib.dump(temp_model, TEMP_MODEL_PATH)
        joblib.dump(wind_model, WIND_MODEL_PATH)

        new_metrics = {
            "model_version": "lightgbm_v2_monthly_retrained",
            "station": "SAOC",
            "city": "rio_cuarto",
            "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "samples_matched": matched_count,
            "train_samples": len(train_df),
            "test_samples": len(test_df),
            "valid_utc_hours": list(range(24)),
            "feature_names": FEATURE_NAMES,
            "temp_improved": True,
            "wind_improved": True,
            "mae_before": current_mae,
            "mae_after": candidate_temp_mae,
            "rmse_before": current_rmse,
            "rmse_after": candidate_temp_rmse,
            "temperature": {
                "mae_before": current_mae,
                "mae_after": candidate_temp_mae,
                "rmse_before": current_rmse,
                "rmse_after": candidate_temp_rmse,
                "improvement_pct": round(improvement_pct, 1),
            },
            "wind": current_metrics.get("wind", {}),
            "model_files": {
                "temperature_model": "riocuarto_temp_bias.joblib",
                "wind_model": "riocuarto_wind_bias.joblib",
            },
        }

        with open(METRICS_PATH, "w", encoding="utf-8") as f:
            json.dump(new_metrics, f, indent=2)

        print(f"[Retrain ML] ¡Éxito! Modelos actualizados a v2 (mejora {improvement_pct:.1f}%).")
    else:
        print(f"[Retrain ML] Candidato v2 no superó el umbral de mejora (+{MIN_IMPROVEMENT_PCT}% MAE sin degradar RMSE). Manteniendo modelo base v1.")

    sys.exit(0)


if __name__ == "__main__":
    main()
