import os
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List, Tuple
from .config import settings


def get_db_path(custom_path: Optional[str] = None) -> str:
    path = custom_path or settings.DATABASE_PATH
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    return path


def get_connection(custom_path: Optional[str] = None) -> sqlite3.Connection:
    conn = sqlite3.connect(get_db_path(custom_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db(custom_path: Optional[str] = None) -> None:
    conn = get_connection(custom_path)
    try:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS weather_cache (
                    key TEXT PRIMARY KEY,
                    payload_json TEXT NOT NULL,
                    updated_at_utc TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS metar_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    icao TEXT NOT NULL,
                    obs_time_utc TEXT NOT NULL,
                    temp_c REAL,
                    wind_kmh REAL,
                    qnh_hpa REAL,
                    raw_metar TEXT,
                    UNIQUE(icao, obs_time_utc)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS forecast_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    created_at_utc TEXT NOT NULL,
                    target_time_utc TEXT NOT NULL,
                    predicted_temp_c REAL,
                    predicted_wind_kmh REAL,
                    predicted_precip_mm REAL,
                    model_source TEXT NOT NULL,
                    UNIQUE(city, target_time_utc, model_source)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    alert_key TEXT NOT NULL,
                    level TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    updated_at_utc TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metar_icao_time ON metar_history(icao, obs_time_utc)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_city_target ON forecast_snapshots(city, target_time_utc)")
    finally:
        conn.close()


def get_cached_payload(key: str, max_age_seconds: int = 900, custom_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    conn = get_connection(custom_path)
    try:
        cur = conn.execute("SELECT payload_json, updated_at_utc FROM weather_cache WHERE key = ?", (key,))
        row = cur.fetchone()
        if not row:
            return None

        updated_at_str = row["updated_at_utc"]
        # Handle 'Z' suffix in ISO string
        if updated_at_str.endswith("Z"):
            updated_at_str = updated_at_str[:-1] + "+00:00"
        updated_at = datetime.fromisoformat(updated_at_str)
        now = datetime.now(timezone.utc)

        if (now - updated_at).total_seconds() > max_age_seconds:
            return None

        return json.loads(row["payload_json"])
    except Exception:
        return None
    finally:
        conn.close()


def set_cached_payload(key: str, payload: Dict[str, Any], custom_path: Optional[str] = None) -> None:
    conn = get_connection(custom_path)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload_str = json.dumps(payload, separators=(",", ":"))
    try:
        with conn:
            conn.execute("""
                INSERT INTO weather_cache (key, payload_json, updated_at_utc)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    payload_json = excluded.payload_json,
                    updated_at_utc = excluded.updated_at_utc
            """, (key, payload_str, now_str))
    finally:
        conn.close()


def save_metar_record(
    icao: str,
    obs_time_utc: str,
    temp_c: Optional[float],
    wind_kmh: Optional[float],
    qnh_hpa: Optional[float],
    raw_metar: str,
    custom_path: Optional[str] = None,
) -> bool:
    """Inserts a METAR observation, ignoring duplicate (icao, obs_time_utc)."""
    conn = get_connection(custom_path)
    try:
        with conn:
            conn.execute("""
                INSERT OR IGNORE INTO metar_history (icao, obs_time_utc, temp_c, wind_kmh, qnh_hpa, raw_metar)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (icao, obs_time_utc, temp_c, wind_kmh, qnh_hpa, raw_metar))
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_metar_history(
    icao: str,
    hours: int = 6,
    custom_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    conn = get_connection(custom_path)
    try:
        cur = conn.execute("""
            SELECT icao, obs_time_utc, temp_c, wind_kmh, qnh_hpa, raw_metar
            FROM metar_history
            WHERE icao = ?
            ORDER BY obs_time_utc DESC
            LIMIT 50
        """, (icao,))
        rows = cur.fetchall()
        result = []
        now = datetime.now(timezone.utc)
        for r in rows:
            t_str = r["obs_time_utc"]
            if t_str.endswith("Z"):
                t_str = t_str[:-1] + "+00:00"
            try:
                obs_dt = datetime.fromisoformat(t_str)
                if (now - obs_dt).total_seconds() <= hours * 3600 + 1800:
                    result.append(dict(r))
            except Exception:
                result.append(dict(r))
        return result
    finally:
        conn.close()


def save_forecast_snapshots(
    city: str,
    snapshots: List[Dict[str, Any]],
    custom_path: Optional[str] = None
) -> int:
    """
    Saves hourly forecast snapshots for precision auditing.
    Snapshots list elements: {target_time_utc, predicted_temp_c, predicted_wind_kmh, predicted_precip_mm, model_source}
    """
    conn = get_connection(custom_path)
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    inserted = 0
    try:
        with conn:
            for s in snapshots:
                conn.execute("""
                    INSERT INTO forecast_snapshots
                    (city, created_at_utc, target_time_utc, predicted_temp_c, predicted_wind_kmh, predicted_precip_mm, model_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(city, target_time_utc, model_source) DO UPDATE SET
                        predicted_temp_c = excluded.predicted_temp_c,
                        predicted_wind_kmh = excluded.predicted_wind_kmh,
                        predicted_precip_mm = excluded.predicted_precip_mm,
                        created_at_utc = excluded.created_at_utc
                """, (
                    city,
                    now_str,
                    s["target_time_utc"],
                    s.get("predicted_temp_c"),
                    s.get("predicted_wind_kmh"),
                    s.get("predicted_precip_mm"),
                    s.get("model_source", "ensemble"),
                ))
                inserted += 1
        return inserted
    except Exception:
        return inserted
    finally:
        conn.close()


def calculate_accuracy_metrics(
    city: str,
    icao: str,
    model_source: str = "ensemble",
    custom_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Computes Mean Absolute Error (MAE) for temperature and wind speed
    by crossing forecast_snapshots with real metar_history.
    """
    conn = get_connection(custom_path)
    try:
        # Match forecast snapshots with metar_history on target_time_utc / obs_time_utc
        # We match on the first 13 characters 'YYYY-MM-DDTHH' (hourly matching)
        cur = conn.execute("""
            SELECT
                s.predicted_temp_c,
                m.temp_c AS actual_temp_c,
                s.predicted_wind_kmh,
                m.wind_kmh AS actual_wind_kmh,
                s.target_time_utc,
                m.obs_time_utc
            FROM forecast_snapshots s
            JOIN metar_history m
                ON m.icao = ?
                AND substr(s.target_time_utc, 1, 13) = substr(m.obs_time_utc, 1, 13)
            WHERE s.city = ?
              AND s.model_source = ?
              AND m.temp_c IS NOT NULL
              AND s.predicted_temp_c IS NOT NULL
            ORDER BY s.target_time_utc DESC
            LIMIT 168
        """, (icao, city, model_source))

        rows = cur.fetchall()
        if not rows:
            return {
                "city": city,
                "icao": icao,
                "model_source": model_source,
                "samples_count": 0,
                "mae_temperature_c": None,
                "mae_wind_kmh": None,
                "status": "insufficient_history",
                "message": "Aún no hay suficientes pares de pronóstico histórico vs METAR real guardados en base de datos.",
            }

        temp_errors = [abs(r["predicted_temp_c"] - r["actual_temp_c"]) for r in rows if r["predicted_temp_c"] is not None and r["actual_temp_c"] is not None]
        wind_errors = [abs(r["predicted_wind_kmh"] - r["actual_wind_kmh"]) for r in rows if r["predicted_wind_kmh"] is not None and r["actual_wind_kmh"] is not None]

        mae_temp = round(sum(temp_errors) / len(temp_errors), 2) if temp_errors else None
        mae_wind = round(sum(wind_errors) / len(wind_errors), 2) if wind_errors else None

        return {
            "city": city,
            "icao": icao,
            "model_source": model_source,
            "samples_count": len(rows),
            "mae_temperature_c": mae_temp,
            "mae_wind_kmh": mae_wind,
            "status": "ok",
        }
    finally:
        conn.close()
