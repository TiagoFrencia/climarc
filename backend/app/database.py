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
                    updated_at_utc TEXT NOT NULL,
                    last_severity TEXT,
                    last_sent_utc TEXT,
                    payload_summary TEXT
                )
            """)
            # Migration check: Ensure new columns exist if table was created in an earlier phase
            cur = conn.execute("PRAGMA table_info(alert_state)")
            existing_cols = [r["name"] for r in cur.fetchall()]
            if "last_severity" not in existing_cols:
                conn.execute("ALTER TABLE alert_state ADD COLUMN last_severity TEXT")
            if "last_sent_utc" not in existing_cols:
                conn.execute("ALTER TABLE alert_state ADD COLUMN last_sent_utc TEXT")
            if "payload_summary" not in existing_cols:
                conn.execute("ALTER TABLE alert_state ADD COLUMN payload_summary TEXT")

            conn.execute("""
                CREATE TABLE IF NOT EXISTS alert_settings (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    rain_prob_pct REAL NOT NULL,
                    rain_mm REAL NOT NULL,
                    wrf_nearby_mm REAL NOT NULL,
                    gust_kmh REAL NOT NULL,
                    temp_drop_c REAL NOT NULL,
                    cold_apparent_c REAL NOT NULL,
                    cape_jkg REAL NOT NULL,
                    cooldown_hours REAL NOT NULL,
                    quiet_enabled INTEGER NOT NULL DEFAULT 0,
                    quiet_start_hour INTEGER NOT NULL DEFAULT 23,
                    quiet_end_hour INTEGER NOT NULL DEFAULT 7,
                    updated_at_utc TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_metar_icao_time ON metar_history(icao, obs_time_utc)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_city_target ON forecast_snapshots(city, target_time_utc)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_alert_state_city_key ON alert_state(city, alert_key)")
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


def get_alert_settings(custom_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Retrieves alert thresholds and quiet hours from SQLite.
    If no record exists (e.g. after ephemeral disk restart), falls back to settings (.env defaults).
    """
    conn = get_connection(custom_path)
    try:
        cur = conn.execute("""
            SELECT
                rain_prob_pct, rain_mm, wrf_nearby_mm, gust_kmh,
                temp_drop_c, cold_apparent_c, cape_jkg, cooldown_hours,
                quiet_enabled, quiet_start_hour, quiet_end_hour, updated_at_utc
            FROM alert_settings
            WHERE id = 1
        """)
        row = cur.fetchone()
        if not row:
            return {
                "rain_prob_pct": float(settings.ALERT_RAIN_PROB_PCT),
                "rain_mm": float(settings.ALERT_RAIN_MM),
                "wrf_nearby_mm": float(settings.ALERT_WRF_NEARBY_MM),
                "gust_kmh": float(settings.ALERT_GUST_KMH),
                "temp_drop_c": float(settings.ALERT_TEMP_DROP_C),
                "cold_apparent_c": float(settings.ALERT_COLD_APPARENT_C),
                "cape_jkg": float(settings.ALERT_CAPE_JKG),
                "cooldown_hours": float(settings.ALERT_COOLDOWN_HOURS),
                "quiet_enabled": bool(settings.ALERT_QUIET_ENABLED),
                "quiet_start_hour": int(settings.ALERT_QUIET_START_HOUR),
                "quiet_end_hour": int(settings.ALERT_QUIET_END_HOUR),
                "updated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        return {
            "rain_prob_pct": float(row["rain_prob_pct"]),
            "rain_mm": float(row["rain_mm"]),
            "wrf_nearby_mm": float(row["wrf_nearby_mm"]),
            "gust_kmh": float(row["gust_kmh"]),
            "temp_drop_c": float(row["temp_drop_c"]),
            "cold_apparent_c": float(row["cold_apparent_c"]),
            "cape_jkg": float(row["cape_jkg"]),
            "cooldown_hours": float(row["cooldown_hours"]),
            "quiet_enabled": bool(row["quiet_enabled"]),
            "quiet_start_hour": int(row["quiet_start_hour"]),
            "quiet_end_hour": int(row["quiet_end_hour"]),
            "updated_at_utc": row["updated_at_utc"],
        }
    finally:
        conn.close()


def update_alert_settings(new_values: Dict[str, Any], custom_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Saves or updates custom thresholds and quiet hours in SQLite.
    Merges unspecified values from current settings.
    """
    current = get_alert_settings(custom_path)
    merged = {**current, **{k: v for k, v in new_values.items() if v is not None}}
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    merged["updated_at_utc"] = now_utc

    conn = get_connection(custom_path)
    try:
        with conn:
            conn.execute("""
                INSERT INTO alert_settings (
                    id, rain_prob_pct, rain_mm, wrf_nearby_mm, gust_kmh,
                    temp_drop_c, cold_apparent_c, cape_jkg, cooldown_hours,
                    quiet_enabled, quiet_start_hour, quiet_end_hour, updated_at_utc
                ) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    rain_prob_pct = excluded.rain_prob_pct,
                    rain_mm = excluded.rain_mm,
                    wrf_nearby_mm = excluded.wrf_nearby_mm,
                    gust_kmh = excluded.gust_kmh,
                    temp_drop_c = excluded.temp_drop_c,
                    cold_apparent_c = excluded.cold_apparent_c,
                    cape_jkg = excluded.cape_jkg,
                    cooldown_hours = excluded.cooldown_hours,
                    quiet_enabled = excluded.quiet_enabled,
                    quiet_start_hour = excluded.quiet_start_hour,
                    quiet_end_hour = excluded.quiet_end_hour,
                    updated_at_utc = excluded.updated_at_utc
            """, (
                float(merged["rain_prob_pct"]),
                float(merged["rain_mm"]),
                float(merged["wrf_nearby_mm"]),
                float(merged["gust_kmh"]),
                float(merged["temp_drop_c"]),
                float(merged["cold_apparent_c"]),
                float(merged["cape_jkg"]),
                float(merged["cooldown_hours"]),
                1 if merged["quiet_enabled"] else 0,
                int(merged["quiet_start_hour"]),
                int(merged["quiet_end_hour"]),
                now_utc,
            ))
        return merged
    finally:
        conn.close()


def get_alert_state(city: str, alert_key: str, custom_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Retrieves the last recorded state of an alert key for anti-spam cooldown evaluation.
    """
    conn = get_connection(custom_path)
    try:
        cur = conn.execute("""
            SELECT city, alert_key, level, active, updated_at_utc, last_severity, last_sent_utc, payload_summary
            FROM alert_state
            WHERE city = ? AND alert_key = ?
        """, (city, alert_key))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "city": row["city"],
            "alert_key": row["alert_key"],
            "last_severity": row["last_severity"] or row["level"],
            "last_sent_utc": row["last_sent_utc"] or row["updated_at_utc"],
            "payload_summary": row["payload_summary"] or "",
            "active": row["active"],
        }
    finally:
        conn.close()


def set_alert_state(
    city: str,
    alert_key: str,
    last_severity: str,
    payload_summary: str,
    last_sent_utc: Optional[str] = None,
    active: int = 1,
    custom_path: Optional[str] = None
) -> None:
    """
    Records or updates an alert state in SQLite after dispatching.
    """
    conn = get_connection(custom_path)
    now_utc = last_sent_utc or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    try:
        with conn:
            conn.execute("""
                INSERT INTO alert_state (
                    city, alert_key, level, active, updated_at_utc,
                    last_severity, last_sent_utc, payload_summary
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(city, alert_key) DO UPDATE SET
                    level = excluded.level,
                    active = excluded.active,
                    updated_at_utc = excluded.updated_at_utc,
                    last_severity = excluded.last_severity,
                    last_sent_utc = excluded.last_sent_utc,
                    payload_summary = excluded.payload_summary
            """, (
                city,
                alert_key,
                last_severity,
                active,
                now_utc,
                last_severity,
                now_utc,
                payload_summary,
            ))
    finally:
        conn.close()


def get_recent_alerts(city: Optional[str] = None, limit: int = 10, custom_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Returns recent alerts sent or tracked in SQLite.
    """
    conn = get_connection(custom_path)
    try:
        query = """
            SELECT city, alert_key, COALESCE(last_severity, level) as severity,
                   COALESCE(last_sent_utc, updated_at_utc) as last_sent_utc,
                   payload_summary
            FROM alert_state
        """
        params = []
        if city:
            query += " WHERE city = ?"
            params.append(city)
        query += " ORDER BY COALESCE(last_sent_utc, updated_at_utc) DESC LIMIT ?"
        params.append(limit)

        cur = conn.execute(query, tuple(params))
        rows = cur.fetchall()
        return [
            {
                "city": r["city"],
                "alert_key": r["alert_key"],
                "severity": r["severity"],
                "last_sent_utc": r["last_sent_utc"],
                "payload_summary": r["payload_summary"] or "",
            }
            for r in rows
        ]
    finally:
        conn.close()
