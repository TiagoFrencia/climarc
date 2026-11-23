"""
Phase 3: Local Bias Corrector (LightGBM) Training Script using Real SAOC Observations
Downloads real runway ground truth from Iowa Environmental Mesonet (IEM) for SAOC (Río Cuarto)
and historical multi-model forecast from Open-Meteo Historical Forecast API.
Trains two lightweight LightGBM models for temperature and wind bias correction.
"""

import os
import sys
import json
import time
import math
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple, Optional

import joblib
import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

# Paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(BASE_DIR, "data", "training_cache")
MODELS_DIR = os.path.join(BASE_DIR, "backend", "models")

os.makedirs(CACHE_DIR, exist_ok=True)
os.makedirs(MODELS_DIR, exist_ok=True)

SAOC_CSV_CACHE = os.path.join(CACHE_DIR, "saoc_iem_raw.csv")
OPEN_METEO_CACHE = os.path.join(CACHE_DIR, "open_meteo_historical.json")
MATCHED_DATASET_CACHE = os.path.join(CACHE_DIR, "matched_dataset.json")

TEMP_MODEL_PATH = os.path.join(MODELS_DIR, "riocuarto_temp_bias.joblib")
WIND_MODEL_PATH = os.path.join(MODELS_DIR, "riocuarto_wind_bias.joblib")
METRICS_PATH = os.path.join(MODELS_DIR, "metrics.json")

# Río Cuarto coordinates
LAT = -33.1232
LON = -64.3493

# 6-month download blocks for SAOC ground truth spanning past 24+ months
IEM_BLOCKS = [
    ("2022", "4", "1", "2022", "10", "31"),
    ("2022", "11", "1", "2023", "4", "30"),
    ("2023", "5", "1", "2023", "10", "31"),
    ("2023", "11", "1", "2024", "4", "30"),
    ("2024", "5", "1", "2024", "11", "23"),
]

# Historical Open-Meteo download blocks
OPEN_METEO_BLOCKS = [
    ("2022-04-01", "2022-12-31"),
    ("2023-01-01", "2023-12-31"),
    ("2024-01-01", "2024-11-23"),
]

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


def download_iem_observations() -> List[Dict[str, Any]]:
    """
    Downloads real SAOC METAR observations from IEM in 6-month blocks.
    Parses and cleans observations, aligning within +-20 min of exact UTC hour.
    Caches results locally.
    """
    if os.path.exists(SAOC_CSV_CACHE):
        print(f"[IEM] Loading cached ground truth from {SAOC_CSV_CACHE}")
        with open(SAOC_CSV_CACHE, "r", encoding="utf-8") as f:
            all_lines = [ln.strip() for ln in f if ln.strip()]
    else:
        print("[IEM] Downloading SAOC runway observations from Iowa Environmental Mesonet...")
        all_lines = []
        for idx, (y1, m1, d1, y2, m2, d2) in enumerate(IEM_BLOCKS):
            time.sleep(1.5)
            url = (
                f"https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?"
                f"station=SAOC&data=tmpc&data=dwpc&data=relh&data=sknt&data=gust&data=drct&data=mslp&"
                f"year1={y1}&month1={m1}&day1={d1}&year2={y2}&month2={m2}&day2={d2}&"
                f"tz=Etc/UTC&format=onlycomma&missing=null&report_type=3&report_type=4"
            )
            print(f"  Downloading block {idx + 1}/{len(IEM_BLOCKS)}: {y1}-{m1:0>2}-{d1:0>2} to {y2}-{m2:0>2}-{d2:0>2}...")
            req = urllib.request.Request(url, headers={"User-Agent": "ClimaRio-Trainer/1.0"})
            try:
                with urllib.request.urlopen(req) as resp:
                    raw_text = resp.read().decode("utf-8")
                    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
                    if not all_lines and lines:
                        # Keep header
                        all_lines.extend(lines)
                    elif len(lines) > 1:
                        # Skip header for subsequent blocks
                        all_lines.extend(lines[1:])
            except Exception as exc:
                print(f"  [Warning] Failed block {y1}-{y2}: {exc}")

        with open(SAOC_CSV_CACHE, "w", encoding="utf-8") as f:
            f.write("\n".join(all_lines) + "\n")
        print(f"[IEM] Downloaded {len(all_lines)} raw rows and cached to {SAOC_CSV_CACHE}")

    # Process and align to exact UTC hour within +-20 min
    # Header: station,valid,tmpc,dwpc,relh,sknt,gust,drct,mslp
    header = all_lines[0].split(",") if all_lines else []
    col_idx = {name: i for i, name in enumerate(header)}

    # Map candidate hourly timestamp -> best observation (closest to :00)
    hourly_obs: Dict[str, Tuple[float, Dict[str, Any]]] = {}

    for line in all_lines[1:]:
        parts = line.split(",")
        if len(parts) < len(col_idx):
            continue

        valid_str = parts[col_idx["valid"]]
        tmpc_str = parts[col_idx["tmpc"]]
        sknt_str = parts[col_idx["sknt"]]
        relh_str = parts[col_idx["relh"]]
        drct_str = parts[col_idx["drct"]]
        mslp_str = parts[col_idx["mslp"]]

        # Require at least temperature or wind
        if tmpc_str in ("null", "", "None") and sknt_str in ("null", "", "None"):
            continue

        try:
            dt = datetime.strptime(valid_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # Alignment logic: within +-20 min of :00
        minute = dt.minute
        if minute <= 20:
            target_dt = dt.replace(minute=0, second=0)
            diff_min = float(minute)
        elif minute >= 40:
            target_dt = (dt + timedelta(hours=1)).replace(minute=0, second=0)
            diff_min = float(60 - minute)
        else:
            # Between :21 and :39, skip
            continue

        target_iso = target_dt.strftime("%Y-%m-%dT%H:00:00Z")

        # Parse numerical values
        temp_c = float(tmpc_str) if tmpc_str not in ("null", "", "None") else None
        wind_kt = float(sknt_str) if sknt_str not in ("null", "", "None") else None
        wind_kmh = round(wind_kt * 1.852, 1) if wind_kt is not None else None
        relh_pct = float(relh_str) if relh_str not in ("null", "", "None") else None
        wind_dir = float(drct_str) if drct_str not in ("null", "", "None") else None
        mslp_hpa = float(mslp_str) if mslp_str not in ("null", "", "None") else None

        record = {
            "timestamp_utc": target_iso,
            "obs_time_raw": valid_str,
            "real_temp_c": temp_c,
            "real_wind_kmh": wind_kmh,
            "real_rh_pct": relh_pct,
            "real_wind_dir_deg": wind_dir,
            "real_mslp_hpa": mslp_hpa,
        }

        # Keep the record closest to :00
        if target_iso not in hourly_obs or diff_min < hourly_obs[target_iso][0]:
            hourly_obs[target_iso] = (diff_min, record)

    cleaned = [val[1] for val in hourly_obs.values()]
    cleaned.sort(key=lambda x: x["timestamp_utc"])
    print(f"[IEM] Aligned {len(cleaned)} unique hourly observations within +-20 min of :00")
    return cleaned


def download_open_meteo_historical() -> Dict[str, Dict[str, Any]]:
    """
    Downloads historical forecast from Open-Meteo Historical Forecast API for Río Cuarto.
    Caches raw responses locally.
    Computes production ensemble without WRF: ECMWF 55%, ICON 25%, GFS 20% (with automatic renormalization).
    """
    if os.path.exists(OPEN_METEO_CACHE):
        print(f"[Open-Meteo] Loading cached historical forecast from {OPEN_METEO_CACHE}")
        with open(OPEN_METEO_CACHE, "r", encoding="utf-8") as f:
            all_hourly = json.load(f)
    else:
        print("[Open-Meteo] Downloading historical forecast from Open-Meteo Historical Forecast API...")
        all_hourly = {}
        for start_d, end_d in OPEN_METEO_BLOCKS:
            time.sleep(1.0)
            url = (
                f"https://historical-forecast-api.open-meteo.com/v1/forecast?"
                f"latitude={LAT}&longitude={LON}&start_date={start_d}&end_date={end_d}&"
                f"models=ecmwf_ifs025,icon_seamless,gfs_seamless&"
                f"hourly=temperature_2m,relative_humidity_2m,pressure_msl,precipitation,wind_speed_10m,wind_direction_10m&"
                f"timezone=UTC"
            )
            print(f"  Downloading Open-Meteo block: {start_d} to {end_d}...")
            req = urllib.request.Request(url, headers={"User-Agent": "ClimaRio-Trainer/1.0"})
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                hourly = data.get("hourly", {})
                times = hourly.get("time", [])
                for i, t in enumerate(times):
                    iso_t = f"{t}:00Z" if len(t) == 16 else f"{t}Z"
                    rec = {}
                    for k, vals in hourly.items():
                        if k != "time":
                            rec[k] = vals[i]
                    all_hourly[iso_t] = rec

        with open(OPEN_METEO_CACHE, "w", encoding="utf-8") as f:
            json.dump(all_hourly, f)
        print(f"[Open-Meteo] Downloaded {len(all_hourly)} hourly steps and cached to {OPEN_METEO_CACHE}")

    # Compute production base ensemble forecast without WRF: ECMWF 55%, ICON 25%, GFS 20%
    base_weights = {
        "ecmwf_ifs025": 0.55,
        "icon_seamless": 0.25,
        "gfs_seamless": 0.20,
    }

    forecast_by_hour: Dict[str, Dict[str, Any]] = {}
    sorted_times = sorted(all_hourly.keys())

    # Pre-calculate rolling pred_temp for temp_trend_3h
    temp_series: Dict[str, float] = {}

    for t_iso in sorted_times:
        rec = all_hourly[t_iso]

        def _get_weighted(var_name: str) -> Optional[float]:
            vals = {}
            for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]:
                val = rec.get(f"{var_name}_{m}")
                if val is not None and not math.isnan(val):
                    vals[m] = float(val)
            if not vals:
                return None
            tot_w = sum(base_weights[m] for m in vals)
            return sum(vals[m] * (base_weights[m] / tot_w) for m in vals)

        pred_t = _get_weighted("temperature_2m")
        pred_rh = _get_weighted("relative_humidity_2m")
        pred_p = _get_weighted("pressure_msl")
        pred_precip = _get_weighted("precipitation")
        pred_w = _get_weighted("wind_speed_10m")

        # Wind direction vector average
        dir_vals = {}
        for m in ["ecmwf_ifs025", "icon_seamless", "gfs_seamless"]:
            d_val = rec.get(f"wind_direction_10m_{m}")
            if d_val is not None and not math.isnan(d_val):
                dir_vals[m] = float(d_val)

        if dir_vals:
            tot_w = sum(base_weights[m] for m in dir_vals)
            u_sum = sum((base_weights[m] / tot_w) * (-math.sin(math.radians(dir_vals[m]))) for m in dir_vals)
            v_sum = sum((base_weights[m] / tot_w) * (-math.cos(math.radians(dir_vals[m]))) for m in dir_vals)
            pred_wdir = math.degrees(math.atan2(-u_sum, -v_sum)) % 360.0
        else:
            pred_wdir = 0.0

        if pred_t is not None:
            temp_series[t_iso] = pred_t

        forecast_by_hour[t_iso] = {
            "timestamp_utc": t_iso,
            "pred_temp_c": pred_t,
            "pred_rh_pct": pred_rh,
            "pred_pressure_msl": pred_p,
            "pred_precip_mm": pred_precip,
            "pred_wind_kmh": pred_w,
            "pred_wind_dir_deg": pred_wdir,
        }

    # Second pass for temp_trend_3h (pred_temp(t) - pred_temp(t-3h))
    for t_iso, fc in forecast_by_hour.items():
        try:
            curr_dt = datetime.fromisoformat(t_iso.replace("Z", "+00:00"))
            past_dt = curr_dt - timedelta(hours=3)
            past_iso = past_dt.strftime("%Y-%m-%dT%H:00:00Z")
            if past_iso in temp_series and fc["pred_temp_c"] is not None:
                fc["temp_trend_3h"] = round(fc["pred_temp_c"] - temp_series[past_iso], 2)
            else:
                fc["temp_trend_3h"] = 0.0
        except Exception:
            fc["temp_trend_3h"] = 0.0

    print(f"[Open-Meteo] Processed base ensemble forecast for {len(forecast_by_hour)} hourly steps")
    return forecast_by_hour


def match_and_engineer_features(
    observations: List[Dict[str, Any]],
    forecasts: Dict[str, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Inner joins real observations with historical forecast by timestamp.
    Calculates features:
    - hour_sin, hour_cos, doy_sin, doy_cos
    - pred_temp_c, pred_rh_pct, pred_pressure_msl, pred_precip_mm
    - pred_wind_kmh, wind_u, wind_v
    - temp_trend_3h
    - Targets:
      - temp_bias = real_temp_c - pred_temp_c
      - wind_bias = real_wind_kmh - pred_wind_kmh
    """
    matched = []
    for obs in observations:
        t_iso = obs["timestamp_utc"]
        if t_iso not in forecasts:
            continue

        fc = forecasts[t_iso]

        # Require all essential predictions and ground truth
        if fc["pred_temp_c"] is None or obs["real_temp_c"] is None:
            continue
        if fc["pred_wind_kmh"] is None or obs["real_wind_kmh"] is None:
            continue
        if fc["pred_pressure_msl"] is None:
            continue

        dt = datetime.fromisoformat(t_iso.replace("Z", "+00:00"))
        hour = dt.hour
        doy = dt.timetuple().tm_yday

        hour_sin = math.sin(2.0 * math.pi * hour / 24.0)
        hour_cos = math.cos(2.0 * math.pi * hour / 24.0)
        doy_sin = math.sin(2.0 * math.pi * doy / 365.25)
        doy_cos = math.cos(2.0 * math.pi * doy / 365.25)

        w_kmh = fc["pred_wind_kmh"]
        w_dir = fc.get("pred_wind_dir_deg", 0.0)
        rad = math.radians(w_dir)
        # Met components
        wind_u = -w_kmh * math.sin(rad)
        wind_v = -w_kmh * math.cos(rad)

        matched.append({
            "timestamp_utc": t_iso,
            "hour_utc": hour,
            "real_temp_c": obs["real_temp_c"],
            "real_wind_kmh": obs["real_wind_kmh"],
            "features": {
                "hour_sin": hour_sin,
                "hour_cos": hour_cos,
                "doy_sin": doy_sin,
                "doy_cos": doy_cos,
                "pred_temp_c": fc["pred_temp_c"],
                "pred_rh_pct": fc["pred_rh_pct"] if fc["pred_rh_pct"] is not None else 50.0,
                "pred_pressure_msl": fc["pred_pressure_msl"],
                "pred_precip_mm": fc["pred_precip_mm"] if fc["pred_precip_mm"] is not None else 0.0,
                "pred_wind_kmh": w_kmh,
                "wind_u": wind_u,
                "wind_v": wind_v,
                "temp_trend_3h": fc.get("temp_trend_3h", 0.0),
            },
            "temp_target": obs["real_temp_c"] - fc["pred_temp_c"],
            "wind_target": obs["real_wind_kmh"] - w_kmh,
        })

    matched.sort(key=lambda x: x["timestamp_utc"])
    return matched


def audit_coverage(matched: List[Dict[str, Any]]) -> List[int]:
    """
    Audits observation coverage across 24 UTC hours.
    Returns valid_utc_hours: hours with >= 40 samples in past 24 months.
    """
    counts = {h: 0 for h in range(24)}
    for item in matched:
        counts[item["hour_utc"]] += 1

    print("\n" + "=" * 65)
    print("  AUDITORÍA DE COBERTURA HORARIA REAL SAOC (00 a 23 UTC)")
    print("=" * 65)
    print(f" Total horas emparejadas con verdad terrestre: {len(matched)}")
    print("-" * 65)
    print(" Hora UTC | Muestras | Cobertura | Estado")
    print("-" * 65)

    valid_hours = []
    for h in range(24):
        cnt = counts[h]
        status = "VÁLIDA (>= 40)" if cnt >= 40 else "HUECO NOCTURNO (< 40)"
        cobertura = "Plena" if cnt >= 300 else ("Media" if cnt >= 100 else ("Baja" if cnt >= 40 else "Nula/Pobre"))
        print(f"   {h:02d}:00   |   {cnt:5d}  |   {cobertura:7s} | {status}")
        if cnt >= 40:
            valid_hours.append(h)

    print("-" * 65)
    print(f" Horas UTC válidas para inferencia ML: {valid_hours}")
    print("=" * 65 + "\n")

    return valid_hours


def train_models(matched: List[Dict[str, Any]], valid_utc_hours: List[int]):
    """
    Strict chronological split: first 80% train, last 20% test.
    Trains LightGBM models for temperature and wind bias.
    Evaluates Before vs After MAE/RMSE (global and day vs night).
    Saves models and metrics.json.
    """
    n_total = len(matched)
    split_idx = int(n_total * 0.80)

    train_data = matched[:split_idx]
    test_data = matched[split_idx:]

    print(f"Chronological split: {len(train_data)} train samples (80%), {len(test_data)} test samples (20%)")
    print(f"Train period: {train_data[0]['timestamp_utc']} -> {train_data[-1]['timestamp_utc']}")
    print(f"Test period:  {test_data[0]['timestamp_utc']} -> {test_data[-1]['timestamp_utc']}")

    # Build DataFrame
    X_train_df = pd.DataFrame([{f: item["features"][f] for f in FEATURE_NAMES} for item in train_data], columns=FEATURE_NAMES)
    y_train_temp_full = np.array([item["temp_target"] for item in train_data])
    y_train_wind_full = np.array([item["wind_target"] for item in train_data])

    X_test_df = pd.DataFrame([{f: item["features"][f] for f in FEATURE_NAMES} for item in test_data], columns=FEATURE_NAMES)
    y_test_temp_real = np.array([item["real_temp_c"] for item in test_data])
    y_test_wind_real = np.array([item["real_wind_kmh"] for item in test_data])

    pred_temp_test = np.array([item["features"]["pred_temp_c"] for item in test_data])
    pred_wind_test = np.array([item["features"]["pred_wind_kmh"] for item in test_data])

    # Validation subset inside train (last 15% of train) for early stopping
    val_split = int(len(X_train_df) * 0.85)
    X_tr, X_val = X_train_df.iloc[:val_split], X_train_df.iloc[val_split:]
    y_tr_temp, y_val_temp = y_train_temp_full[:val_split], y_train_temp_full[val_split:]
    y_tr_wind, y_val_wind = y_train_wind_full[:val_split], y_train_wind_full[val_split:]

    # 1. Temperature Bias Model
    print("\n[LightGBM] Training Temperature Bias Model...")
    temp_model = lgb.LGBMRegressor(
        max_depth=5,
        num_leaves=20,
        learning_rate=0.05,
        min_child_samples=30,
        n_estimators=500,
        random_state=42,
        verbose=-1,
    )
    temp_model.fit(
        X_tr,
        y_tr_temp,
        eval_set=[(X_val, y_val_temp)],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    print(f"  Best iteration (trees): {temp_model.best_iteration_}")

    # 2. Wind Bias Model
    print("[LightGBM] Training Wind Bias Model...")
    wind_model = lgb.LGBMRegressor(
        max_depth=5,
        num_leaves=20,
        learning_rate=0.05,
        min_child_samples=30,
        n_estimators=500,
        random_state=42,
        verbose=-1,
    )
    wind_model.fit(
        X_tr,
        y_tr_wind,
        eval_set=[(X_val, y_val_wind)],
        callbacks=[lgb.early_stopping(stopping_rounds=30, verbose=False)],
    )
    print(f"  Best iteration (trees): {wind_model.best_iteration_}")

    # Evaluation on 20% unseen test set
    test_pred_temp_bias = temp_model.predict(X_test_df)
    test_pred_wind_bias = wind_model.predict(X_test_df)

    # Clamping physical boundaries
    test_pred_temp_bias = np.clip(test_pred_temp_bias, -4.5, 4.5)
    test_pred_wind_bias = np.clip(test_pred_wind_bias, -18.0, 18.0)

    # Safe apply: only for valid_utc_hours
    test_hours = np.array([item["hour_utc"] for item in test_data])
    valid_mask = np.isin(test_hours, valid_utc_hours)

    corrected_temp = pred_temp_test.copy()
    corrected_temp[valid_mask] += test_pred_temp_bias[valid_mask]

    corrected_wind = pred_wind_test.copy()
    corrected_wind[valid_mask] = np.maximum(0.0, corrected_wind[valid_mask] + test_pred_wind_bias[valid_mask])

    # Compute Metrics
    # Global
    temp_mae_before = float(mean_absolute_error(y_test_temp_real, pred_temp_test))
    temp_mae_after = float(mean_absolute_error(y_test_temp_real, corrected_temp))
    temp_rmse_before = float(root_mean_squared_error(y_test_temp_real, pred_temp_test))
    temp_rmse_after = float(root_mean_squared_error(y_test_temp_real, corrected_temp))

    wind_mae_before = float(mean_absolute_error(y_test_wind_real, pred_wind_test))
    wind_mae_after = float(mean_absolute_error(y_test_wind_real, corrected_wind))
    wind_rmse_before = float(root_mean_squared_error(y_test_wind_real, pred_wind_test))
    wind_rmse_after = float(root_mean_squared_error(y_test_wind_real, corrected_wind))

    # Day vs Night: Day = 10 to 22 UTC (07:00 to 19:00 local UTC-3)
    day_mask = (test_hours >= 10) & (test_hours < 22)
    night_mask = ~day_mask

    day_temp_mae_before = float(mean_absolute_error(y_test_temp_real[day_mask], pred_temp_test[day_mask]))
    day_temp_mae_after = float(mean_absolute_error(y_test_temp_real[day_mask], corrected_temp[day_mask]))
    night_temp_mae_before = float(mean_absolute_error(y_test_temp_real[night_mask], pred_temp_test[night_mask]))
    night_temp_mae_after = float(mean_absolute_error(y_test_temp_real[night_mask], corrected_temp[night_mask]))

    day_wind_mae_before = float(mean_absolute_error(y_test_wind_real[day_mask], pred_wind_test[day_mask]))
    day_wind_mae_after = float(mean_absolute_error(y_test_wind_real[day_mask], corrected_wind[day_mask]))
    night_wind_mae_before = float(mean_absolute_error(y_test_wind_real[night_mask], pred_wind_test[night_mask]))
    night_wind_mae_after = float(mean_absolute_error(y_test_wind_real[night_mask], corrected_wind[night_mask]))

    # Print comparative table
    print("\n" + "=" * 80)
    print("  RESULTADOS DE EVALUACIÓN EN SET DE TEST (20% MÁS RECIENTE, NO VISTO)")
    print("=" * 80)
    print(f" Muestras de Test: {len(test_data)} horas (Día: {sum(day_mask)}, Noche: {sum(night_mask)})")
    print("-" * 80)
    print(f" Variable        | Métrica     | Antes (Base) | Después (ML) | Mejora (%) | Status")
    print("-" * 80)
    t_mae_imp = (temp_mae_before - temp_mae_after) / temp_mae_before * 100.0
    t_rmse_imp = (temp_rmse_before - temp_rmse_after) / temp_rmse_before * 100.0
    w_mae_imp = (wind_mae_before - wind_mae_after) / wind_mae_before * 100.0
    w_rmse_imp = (wind_rmse_before - wind_rmse_after) / wind_rmse_before * 100.0

    print(f" Temperatura °C  | MAE Global  | {temp_mae_before:10.2f} °C | {temp_mae_after:10.2f} °C | {t_mae_imp:8.1f}%  | {'MEJORA' if t_mae_imp > 0 else 'SIN MEJORA'}")
    print(f" Temperatura °C  | RMSE Global | {temp_rmse_before:10.2f} °C | {temp_rmse_after:10.2f} °C | {t_rmse_imp:8.1f}%  | {'MEJORA' if t_rmse_imp > 0 else 'SIN MEJORA'}")
    print(f" Temperatura °C  | MAE Día     | {day_temp_mae_before:10.2f} °C | {day_temp_mae_after:10.2f} °C | {(day_temp_mae_before - day_temp_mae_after) / day_temp_mae_before * 100:8.1f}%  | {'MEJORA' if day_temp_mae_after < day_temp_mae_before else 'SIN MEJORA'}")
    print(f" Temperatura °C  | MAE Noche   | {night_temp_mae_before:10.2f} °C | {night_temp_mae_after:10.2f} °C | {(night_temp_mae_before - night_temp_mae_after) / night_temp_mae_before * 100:8.1f}%  | {'MEJORA' if night_temp_mae_after < night_temp_mae_before else 'SIN MEJORA'}")
    print("-" * 80)
    print(f" Viento km/h     | MAE Global  | {wind_mae_before:8.2f} km/h | {wind_mae_after:8.2f} km/h | {w_mae_imp:8.1f}%  | {'MEJORA' if w_mae_imp > 0 else 'SIN MEJORA'}")
    print(f" Viento km/h     | RMSE Global | {wind_rmse_before:8.2f} km/h | {wind_rmse_after:8.2f} km/h | {w_rmse_imp:8.1f}%  | {'MEJORA' if w_rmse_imp > 0 else 'SIN MEJORA'}")
    print(f" Viento km/h     | MAE Día     | {day_wind_mae_before:8.2f} km/h | {day_wind_mae_after:8.2f} km/h | {(day_wind_mae_before - day_wind_mae_after) / day_wind_mae_before * 100:8.1f}%  | {'MEJORA' if day_wind_mae_after < day_wind_mae_before else 'SIN MEJORA'}")
    print(f" Viento km/h     | MAE Noche   | {night_wind_mae_before:8.2f} km/h | {night_wind_mae_after:8.2f} km/h | {(night_wind_mae_before - night_wind_mae_after) / night_wind_mae_before * 100:8.1f}%  | {'MEJORA' if night_wind_mae_after < night_wind_mae_before else 'SIN MEJORA'}")
    print("=" * 80 + "\n")

    # Save models
    joblib.dump(temp_model, TEMP_MODEL_PATH)
    joblib.dump(wind_model, WIND_MODEL_PATH)

    temp_size_kb = os.path.getsize(TEMP_MODEL_PATH) / 1024.0
    wind_size_kb = os.path.getsize(WIND_MODEL_PATH) / 1024.0

    print(f"Archivos de modelo guardados:")
    print(f"  {TEMP_MODEL_PATH} ({temp_size_kb:.1f} KB)")
    print(f"  {WIND_MODEL_PATH} ({wind_size_kb:.1f} KB)")

    # Save metrics.json
    temp_improved = bool(temp_mae_after < temp_mae_before)
    wind_improved = bool(wind_mae_after < wind_mae_before)

    metrics_payload = {
        "model_version": "lightgbm_v1_saoc",
        "station": "SAOC",
        "city": "rio_cuarto",
        "trained_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "samples_matched": n_total,
        "train_samples": len(train_data),
        "test_samples": len(test_data),
        "valid_utc_hours": valid_utc_hours,
        "feature_names": FEATURE_NAMES,
        "temp_improved": temp_improved,
        "wind_improved": wind_improved,
        "mae_before": round(temp_mae_before, 2),
        "mae_after": round(temp_mae_after, 2),
        "rmse_before": round(temp_rmse_before, 2),
        "rmse_after": round(temp_rmse_after, 2),
        "temperature": {
            "mae_before": round(temp_mae_before, 2),
            "mae_after": round(temp_mae_after, 2),
            "rmse_before": round(temp_rmse_before, 2),
            "rmse_after": round(temp_rmse_after, 2),
            "day_mae_before": round(day_temp_mae_before, 2),
            "day_mae_after": round(day_temp_mae_after, 2),
            "night_mae_before": round(night_temp_mae_before, 2),
            "night_mae_after": round(night_temp_mae_after, 2),
            "improvement_pct": round(t_mae_imp, 1),
        },
        "wind": {
            "mae_before": round(wind_mae_before, 2),
            "mae_after": round(wind_mae_after, 2),
            "rmse_before": round(wind_rmse_before, 2),
            "rmse_after": round(wind_rmse_after, 2),
            "day_mae_before": round(day_wind_mae_before, 2),
            "day_mae_after": round(day_wind_mae_after, 2),
            "night_mae_before": round(night_wind_mae_before, 2),
            "night_mae_after": round(night_wind_mae_after, 2),
            "improvement_pct": round(w_mae_imp, 1),
        },
        "model_files": {
            "temperature_model": os.path.basename(TEMP_MODEL_PATH),
            "temperature_size_kb": round(temp_size_kb, 1),
            "wind_model": os.path.basename(WIND_MODEL_PATH),
            "wind_size_kb": round(wind_size_kb, 1),
        },
    }

    with open(METRICS_PATH, "w", encoding="utf-8") as f:
        json.dump(metrics_payload, f, indent=2)

    print(f"Métricas exportadas a: {METRICS_PATH}")
    return metrics_payload


def main():
    print("=== INICIO FASE 3: ENTRENAMIENTO DE CORRECTOR DE SESGO LOCAL (SAOC) ===")
    observations = download_iem_observations()
    forecasts = download_open_meteo_historical()
    matched = match_and_engineer_features(observations, forecasts)

    if len(matched) < 100:
        print(f"[Error] Only {len(matched)} matched samples. Check data sources.")
        sys.exit(1)

    valid_hours = audit_coverage(matched)
    metrics = train_models(matched, valid_hours)
    print("=== ENTRENAMIENTO Y EVALUACIÓN FINALIZADOS EXITOSAMENTE ===")


if __name__ == "__main__":
    main()
