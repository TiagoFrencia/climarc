import os
import re
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from fastapi.testclient import TestClient
import psycopg

from backend.app.config import Settings, settings
from backend.app.database import (
    translate_to_pg,
    init_db,
    is_postgres,
    check_db_health,
    prune_old_records,
    get_db_session,
    save_metar_record,
    get_metar_history,
    save_forecast_snapshots,
    set_cached_payload,
    get_cached_payload,
)
from backend.app.main import app


def test_translate_to_pg_placeholders():
    """Verifies automatic translation of ? placeholders to %s in PostgreSQL mode."""
    # Basic parameter replacement
    assert translate_to_pg("SELECT * FROM weather WHERE city = ?") == "SELECT * FROM weather WHERE city = %s"
    assert translate_to_pg("INSERT INTO t VALUES (?, ?, ?)") == "INSERT INTO t VALUES (%s, %s, %s)"

    # Preservation of '?' inside string literals
    q_literal = "SELECT * FROM notes WHERE title = 'Is it raining?' AND author = ?"
    expected_literal = "SELECT * FROM notes WHERE title = 'Is it raining?' AND author = %s"
    assert translate_to_pg(q_literal) == expected_literal

    # Preserving escaped quotes
    q_escaped = "SELECT * FROM notes WHERE text = 'It''s 100% chance?' AND id = ?"
    expected_escaped = "SELECT * FROM notes WHERE text = 'It''s 100% chance?' AND id = %s"
    assert translate_to_pg(q_escaped) == expected_escaped

    # No placeholders
    assert translate_to_pg("SELECT 1") == "SELECT 1"


def test_postgres_url_normalization():
    """Verifies that postgres:// is normalized to postgresql:// while keeping empty values clean."""
    s1 = Settings(DATABASE_URL="postgres://user:secret@host.neon.tech/dbname?sslmode=require")
    assert s1.DATABASE_URL == "postgresql://user:secret@host.neon.tech/dbname?sslmode=require"

    s2 = Settings(DATABASE_URL="postgresql://user:secret@host.neon.tech/dbname?sslmode=require")
    assert s2.DATABASE_URL == "postgresql://user:secret@host.neon.tech/dbname?sslmode=require"

    s3 = Settings(DATABASE_URL="")
    assert s3.DATABASE_URL == ""


def test_prune_old_records(tmp_path):
    """Verifies pruning of records older than 90 days in history/snapshots and > 24h in cache."""
    test_db = str(tmp_path / "test_prune.db")
    init_db(custom_path=test_db)

    now = datetime.now(timezone.utc)
    old_95d = (now - timedelta(days=95)).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent_2d = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # 1. Insert METAR records (1 old, 1 recent)
    save_metar_record("SAOC", old_95d, 20.0, 15.0, 1013.0, "METAR SAOC OLD", custom_path=test_db)
    save_metar_record("SAOC", recent_2d, 22.0, 10.0, 1014.0, "METAR SAOC NEW", custom_path=test_db)

    # 2. Insert forecast snapshots (1 old, 1 recent)
    save_forecast_snapshots("rio_cuarto", [
        {"target_time_utc": old_95d, "predicted_temp_c": 19.0, "predicted_wind_kmh": 12.0, "predicted_precip_mm": 0.0},
        {"target_time_utc": recent_2d, "predicted_temp_c": 21.0, "predicted_wind_kmh": 14.0, "predicted_precip_mm": 0.0},
    ], custom_path=test_db)

    # 3. Insert weather cache (1 old > 24h, 1 fresh)
    old_cache_time = (now - timedelta(hours=36)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with get_db_session(custom_path=test_db) as session:
        session.execute(
            "INSERT INTO weather_cache (key, payload_json, updated_at_utc) VALUES (?, ?, ?)",
            ("weather:old", '{"data": "old"}', old_cache_time)
        )
        session.execute(
            "INSERT INTO weather_cache (key, payload_json, updated_at_utc) VALUES (?, ?, ?)",
            ("weather:new", '{"data": "new"}', now.strftime("%Y-%m-%dT%H:%M:%SZ"))
        )

    # Execute pruning (90 days)
    prune_res = prune_old_records(days=90, custom_path=test_db)
    assert prune_res["metar_pruned"] >= 1
    assert prune_res["forecast_pruned"] >= 1
    assert prune_res["cache_pruned"] >= 1

    # Verify that recent METAR remains
    with get_db_session(custom_path=test_db) as session:
        cur = session.execute("SELECT obs_time_utc FROM metar_history")
        metar_times = [r["obs_time_utc"] for r in cur.fetchall()]
        assert recent_2d in metar_times
        assert old_95d not in metar_times

        # Verify that recent cache remains
        cur = session.execute("SELECT key FROM weather_cache")
        cache_keys = [r["key"] for r in cur.fetchall()]
        assert "weather:new" in cache_keys
        assert "weather:old" not in cache_keys


def test_api_health_database_visibility_and_zero_credential_leak():
    """Verifies /api/health exposes engine, persistent, and status without leaking DATABASE_URL or credentials."""
    client = TestClient(app)

    # 1. Check default SQLite response
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    db_source = data["sources"]["database"]
    assert db_source["engine"] == "sqlite"
    assert db_source["persistent"] is False
    assert db_source["status"] == "connected"
    assert "path" in db_source

    # 2. Check PostgreSQL mode health check behavior with simulated credentials
    secret_url = "postgresql://prod_user:SuperSecretPassword999@ep-pooler.us-east-1.aws.neon.tech/neondb?sslmode=require"
    with patch.object(settings, "DATABASE_URL", secret_url):
        with patch("psycopg.connect") as mock_connect:
            mock_conn = MagicMock()
            mock_cur = MagicMock()
            mock_cur.fetchone.return_value = {"?column?": 1}
            mock_conn.cursor.return_value = mock_cur
            mock_conn.__enter__.return_value = mock_conn
            mock_connect.return_value = mock_conn

            health_info = check_db_health()
            assert health_info["engine"] == "postgresql"
            assert health_info["persistent"] is True
            assert health_info["status"] == "connected"
            # Ensure path and credentials are never exposed
            assert "path" not in health_info
            health_str = str(health_info)
            assert "SuperSecretPassword999" not in health_str
            assert "prod_user" not in health_str
            assert "ep-pooler" not in health_str
            assert "neondb" not in health_str


def test_scale_to_zero_retry_on_operational_error():
    """Verifies that get_db_session retries on psycopg.OperationalError to tolerate cold starts."""
    call_count = 0

    def mock_connect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise psycopg.OperationalError("Simulated cold start / scale-to-zero wake-up")
        mock_conn = MagicMock()
        mock_conn.__enter__.return_value = mock_conn
        return mock_conn

    with patch("psycopg.connect", side_effect=mock_connect):
        with patch("time.sleep") as mock_sleep:
            with get_db_session(database_url="postgresql://mock:mock@localhost/db") as session:
                assert session.is_pg is True
            assert call_count == 2
            mock_sleep.assert_called_once_with(1.5)


def test_forbidden_heavy_dependencies_absent():
    """Ensures 0 imports of xarray, s3fs, netCDF4, h5netcdf, or h5py exist in /backend and requirements.txt."""
    backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    forbidden_pattern = re.compile(r"\b(xarray|s3fs|netCDF4|h5netcdf|h5py)\b", re.IGNORECASE)

    violations = []
    for root, dirs, files in os.walk(backend_dir):
        # Skip virtualenvs or cache
        if any(ignored in root for ignored in [".venv", "__pycache__", ".pytest_cache"]):
            continue
        for f in files:
            if (f.endswith(".py") or f == "requirements.txt") and f != "test_database_dual.py":
                filepath = os.path.join(root, f)
                with open(filepath, "r", encoding="utf-8", errors="ignore") as fp:
                    for line_no, line in enumerate(fp, start=1):
                        # Skip this test file itself from self-matching
                        if "test_database_dual.py" in filepath and "forbidden_pattern" in line:
                            continue
                        if forbidden_pattern.search(line):
                            violations.append(f"{filepath}:{line_no} -> {line.strip()}")

    assert not violations, f"Forbidden heavy dependencies found in backend: {violations}"
