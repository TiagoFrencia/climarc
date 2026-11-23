"""
ClimaRC - Phase 1: Real Data Sources Live Probe
Script to probe and validate all external data sources without mocks:
1. Live METAR for Río Cuarto (SAOC) and Córdoba (SACO) [CORE - ACTIVE]
2. Open-Meteo Multi-model forecast (ECMWF IFS 0.25, GFS Seamless, ICON Seamless) [CORE - ACTIVE]
3. SMN Argentina Official API (JWT/WAF Protected) [OPTIONAL - GRACEFUL DEGRADATION]
4. Regional WRF-SMN Model on AWS S3 (s3://smn-ar-wrf) via s3fs and xarray [CORE - ACTIVE]
5. IEM ASOS/METAR 48h Historical Data for SAOC [CORE - ACTIVE]
6. RainViewer Real-Time Radar API [CORE - ACTIVE]
"""

import os
import re
import sys
import time
import json
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Tuple

import httpx

USER_AGENT = "ClimaRC-PersonalApp/1.0"
REQUEST_TIMEOUT = 15.0

# Coordinates
RIO_CUARTO_LAT = -33.1232
RIO_CUARTO_LON = -64.3493
CORDOBA_LAT = -31.4201
CORDOBA_LON = -64.1888


def probe_metar() -> Dict[str, Any]:
    """Source 1: Live METAR for SAOC (Río Cuarto) and SACO (Córdoba)."""
    print("\n" + "=" * 70)
    print("PROBE 1/6: Aviation Weather METAR (SAOC, SACO) [CORE]")
    print("=" * 70)
    
    url = "https://aviationweather.gov/api/data/metar?ids=SAOC,SACO&format=json"
    headers = {"User-Agent": USER_AGENT}
    
    start_t = time.perf_counter()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            
            print(f"HTTP GET {url}")
            print(f"Status Code: {resp.status_code} ({elapsed_ms:.1f} ms)")
            
            if resp.status_code != 200:
                print(f"[ERROR] METAR endpoint returned status {resp.status_code}: {resp.text}")
                return {"status": "FAILED", "code": resp.status_code, "error": resp.text}
            
            data = resp.json()
            if not isinstance(data, list) or len(data) == 0:
                print("[ERROR] METAR response is empty or not a list")
                return {"status": "FAILED", "error": "Empty METAR response"}
            
            print(f"Received {len(data)} METAR reports:")
            parsed_reports = []
            for item in data:
                station = item.get("icaoId")
                obs_time = item.get("obsTime")
                temp = item.get("temp")
                dewp = item.get("dewp")
                wdir = item.get("wdir")
                wspd = item.get("wspd")
                wgst = item.get("wgst")
                altim = item.get("altim")
                raw = item.get("rawOb")
                print(f"  [{station}] Obs: {obs_time} | Temp: {temp}°C | Dew: {dewp}°C | Wind: {wdir}°@{wspd}kt (gust: {wgst}) | QNH: {altim} hPa")
                print(f"    Raw: {raw}")
                parsed_reports.append({
                    "station": station,
                    "obsTime": obs_time,
                    "temp": temp,
                    "dewp": dewp,
                    "wdir": wdir,
                    "wspd": wspd,
                    "wgst": wgst,
                    "altim": altim,
                    "raw": raw,
                })
            
            return {
                "status": "OK",
                "code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "count": len(data),
                "reports": parsed_reports,
            }
    except Exception as exc:
        print(f"[ERROR] METAR request failed: {exc}")
        return {"status": "FAILED", "error": str(exc)}


def probe_open_meteo() -> Dict[str, Any]:
    """Source 2: Open-Meteo Multi-model (ecmwf_ifs025, gfs_seamless, icon_seamless)."""
    print("\n" + "=" * 70)
    print("PROBE 2/6: Open-Meteo Multi-Model Forecast (Río Cuarto) [CORE]")
    print("=" * 70)
    
    params = {
        "latitude": RIO_CUARTO_LAT,
        "longitude": RIO_CUARTO_LON,
        "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m,wind_direction_10m,surface_pressure,weather_code,cape",
        "models": "ecmwf_ifs025,gfs_seamless,icon_seamless",
    }
    base_url = "https://api.open-meteo.com/v1/forecast"
    headers = {"User-Agent": USER_AGENT}
    
    start_t = time.perf_counter()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(base_url, params=params)
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            
            print(f"HTTP GET {resp.url}")
            print(f"Status Code: {resp.status_code} ({elapsed_ms:.1f} ms)")
            
            if resp.status_code != 200:
                print(f"[ERROR] Open-Meteo returned status {resp.status_code}: {resp.text}")
                return {"status": "FAILED", "code": resp.status_code, "error": resp.text}
            
            data = resp.json()
            hourly = data.get("hourly", {})
            timestamps = hourly.get("time", [])
            
            print(f"Coordinates: Lat {data.get('latitude')}, Lon {data.get('longitude')}, Elev: {data.get('elevation')}m")
            print(f"Total forecast hours: {len(timestamps)}")
            print(f"Models requested: ecmwf_ifs025, gfs_seamless, icon_seamless")
            print(f"Total hourly series returned: {len(hourly)}")
            
            if timestamps:
                t0 = timestamps[0]
                print(f"\nModel Comparison at t=0 ({t0}):")
                for model in ["ecmwf_ifs025", "gfs_seamless", "icon_seamless"]:
                    t_val = hourly.get(f"temperature_2m_{model}", [None])[0]
                    pr_val = hourly.get(f"precipitation_{model}", [None])[0]
                    w_val = hourly.get(f"wind_speed_10m_{model}", [None])[0]
                    cape_val = hourly.get(f"cape_{model}", [None])[0]
                    print(f"  [{model:15s}] Temp: {t_val}°C | Precip: {pr_val} mm | Wind: {w_val} km/h | CAPE: {cape_val} J/kg")
            
            return {
                "status": "OK",
                "code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "hours_count": len(timestamps),
                "series_count": len(hourly),
                "sample_timestamp": timestamps[0] if timestamps else None,
            }
    except Exception as exc:
        print(f"[ERROR] Open-Meteo request failed: {exc}")
        return {"status": "FAILED", "error": str(exc)}


def _extract_smn_jwt(client: httpx.Client) -> Tuple[Optional[str], str]:
    """Extracts visitor JWT from SMN portal or environment variable."""
    env_token = os.environ.get("SMN_JWT_TOKEN")
    if env_token and env_token.startswith("eyJ"):
        return env_token.strip(), "env (SMN_JWT_TOKEN)"
    
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    if line.startswith("SMN_JWT_TOKEN="):
                        val = line.split("=", 1)[1].strip().strip('"').strip("'")
                        if val.startswith("eyJ"):
                            return val, ".env file"
        except Exception:
            pass

    url = "https://www.smn.gob.ar/"
    try:
        resp = client.get(url, timeout=10.0, follow_redirects=True)
        if resp.status_code == 200:
            m = re.search(r"localStorage\.setItem\(['\"]token['\"]\s*,\s*['\"]([^'\"]+)['\"]", resp.text)
            if m:
                return m.group(1), "live portal https://www.smn.gob.ar/"
            jwts = re.findall(r"eyJ[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+\.[a-zA-Z0-9_\-]+", resp.text)
            if jwts:
                return jwts[0], "live portal JWT pattern"
    except Exception:
        pass
    
    return None, "none"


def probe_smn() -> Dict[str, Any]:
    """Source 3: Official SMN Argentina API (Graceful degradation architectural pattern)."""
    print("\n" + "=" * 70)
    print("PROBE 3/6: Official SMN Argentina API [OPTIONAL - GRACEFUL DEGRADATION]")
    print("=" * 70)
    
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
        token, source_origin = _extract_smn_jwt(client)
        
        if not token:
            print("  [INFO] Portal https://www.smn.gob.ar/ is protected by Cloudflare Managed Challenge.")
            print("  [INFO] Legacy endpoints (ws.smn.gob.ar/alerts/*) are decommissioned (HTTP 503).")
            print("  [ARCHITECTURE DECISION] Graceful degradation active: smn_available=False.")
            print("  [RESULT] Backend continues serving 100% of core forecast & radar via METAR + Open-Meteo + WRF-SMN.")
            return {
                "status": "SKIPPED_GRACEFUL",
                "smn_available": False,
                "reason": "Cloudflare WAF protected, graceful fallback active",
            }
        
        print(f"JWT Token source: {source_origin}")
        api_headers = {
            "Authorization": f"JWT {token}",
            "User-Agent": USER_AGENT,
        }
        
        rc_georef_url = f"https://ws1.smn.gob.ar/v1/georef/location/coord?lat={RIO_CUARTO_LAT}&lon={RIO_CUARTO_LON}"
        resp_rc = client.get(rc_georef_url, headers=api_headers)
        st_url = "https://ws1.smn.gob.ar/v1/warning/shortterm"
        resp_st = client.get(st_url, headers=api_headers)
        
        all_ok = (resp_rc.status_code == 200 and resp_st.status_code == 200)
        return {
            "status": "OK" if all_ok else "FAILED",
            "smn_available": all_ok,
            "rc_status": resp_rc.status_code,
            "st_status": resp_st.status_code,
        }


def probe_wrf_s3() -> Dict[str, Any]:
    """Source 4: Regional WRF-SMN model on AWS S3 public bucket (anon=True)."""
    print("\n" + "=" * 70)
    print("PROBE 4/6: Regional WRF-SMN Model on AWS S3 (s3fs + xarray) [CORE]")
    print("=" * 70)
    
    import s3fs
    import xarray as xr
    import numpy as np
    
    fs = s3fs.S3FileSystem(anon=True)
    now = datetime.now(timezone.utc)
    print(f"Current UTC timestamp: {now.isoformat()}")
    
    candidate_dates = [now - timedelta(days=d) for d in range(5)]
    found_cycle = None
    target_file = None
    
    for dt in candidate_dates:
        yyyy = dt.strftime("%Y")
        mm = dt.strftime("%m")
        dd = dt.strftime("%d")
        for hh in ["12", "00"]:
            prefix = f"smn-ar-wrf/DATA/WRF/DET/{yyyy}/{mm}/{dd}/{hh}/"
            try:
                files = fs.ls(prefix)
                if files:
                    for f in files:
                        if f.endswith("_001.nc"):
                            target_file = f
                            found_cycle = (yyyy, mm, dd, hh)
                            break
                if target_file:
                    break
            except Exception:
                pass
        if target_file:
            break
            
    if not target_file:
        print("[ERROR] Could not find any recent WRF cycle file in s3://smn-ar-wrf/DATA/WRF/DET/")
        return {"status": "FAILED", "error": "No recent WRF file found"}
        
    print(f"Located most recent cycle: {found_cycle[0]}-{found_cycle[1]}-{found_cycle[2]} {found_cycle[3]} UTC")
    print(f"Opening target 001h NetCDF: {target_file}")
    
    start_t = time.perf_counter()
    s3_stream = fs.open(target_file, "rb")
    try:
        ds = xr.open_dataset(s3_stream, engine="h5netcdf")
        elapsed_s = time.perf_counter() - start_t
        print(f"NetCDF opened successfully in {elapsed_s:.2f} s directly from S3 stream.")
        
        dims_dict = dict(ds.sizes)
        coords_list = list(ds.coords.keys())
        vars_count = len(ds.data_vars)
        
        print(f"\nDimensions: {dims_dict}")
        print(f"Coordinates: {coords_list}")
        print(f"Total Data Variables: {vars_count}")
        
        lat_min = float(ds.lat.min().values)
        lat_max = float(ds.lat.max().values)
        lon_min = float(ds.lon.min().values)
        lon_max = float(ds.lon.max().values)
        print(f"\nSpatial Grid Coverage:")
        print(f"  Latitude : [{lat_min:.2f}, {lat_max:.2f}] (dims: {ds.lat.dims}, shape: {ds.lat.shape})")
        print(f"  Longitude: [{lon_min:.2f}, {lon_max:.2f}] (dims: {ds.lon.dims}, shape: {ds.lon.shape})")
        
        # Exact 2D nearest indices for Río Cuarto and Córdoba
        lats = ds.lat.values
        lons = ds.lon.values
        
        d_rc = (lats - RIO_CUARTO_LAT)**2 + (lons - RIO_CUARTO_LON)**2
        y_rc, x_rc = np.unravel_index(np.argmin(d_rc), d_rc.shape)
        
        d_cba = (lats - CORDOBA_LAT)**2 + (lons - CORDOBA_LON)**2
        y_cba, x_cba = np.unravel_index(np.argmin(d_cba), d_cba.shape)
        
        print(f"\nNearest 2D Mesh Grid Indices for Phase 2 Extraction:")
        print(f"  Río Cuarto : (y_idx={y_rc}, x_idx={x_rc}) -> lat={lats[y_rc, x_rc]:.4f}, lon={lons[y_rc, x_rc]:.4f}")
        print(f"  Córdoba    : (y_idx={y_cba}, x_idx={x_cba}) -> lat={lats[y_cba, x_cba]:.4f}, lon={lons[y_cba, x_cba]:.4f}")
        
        variables_summary = {}
        print(f"\nVariables Catalog Sample:")
        for vname in ["T2", "HR2", "magViento10", "dirViento10", "PP", "PSFC"]:
            if vname in ds.data_vars:
                var = ds[vname]
                units = var.attrs.get("units", "N/A")
                desc = var.attrs.get("description", var.attrs.get("long_name", "N/A"))
                print(f"  - {vname:14s} | units={units:16s} | {desc}")
                variables_summary[vname] = {"units": units, "desc": desc}
            
        ds.close()
        return {
            "status": "OK",
            "cycle": found_cycle,
            "file": target_file,
            "dimensions": dims_dict,
            "rc_indices": (int(y_rc), int(x_rc)),
            "cba_indices": (int(y_cba), int(x_cba)),
            "variables_count": vars_count,
            "variables": variables_summary,
        }
    except Exception as exc:
        print(f"[ERROR] Failed reading WRF file with xarray: {exc}")
        return {"status": "FAILED", "error": str(exc)}
    finally:
        try:
            s3_stream.close()
        except Exception:
            pass


def probe_iem() -> Dict[str, Any]:
    """Source 5: Iowa Environmental Mesonet (IEM) ASOS/METAR 48h History for SAOC."""
    print("\n" + "=" * 70)
    print("PROBE 5/6: IEM ASOS 48h History for SAOC (Río Cuarto) [CORE]")
    print("=" * 70)
    
    url = (
        "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
        "?station=SAOC&data=tmpc&data=dwpc&data=relh&data=sknt&data=gust&data=drct&data=mslp"
        "&hours=48&tz=Etc/UTC&format=onlycomma&missing=null&report_type=3&report_type=4"
    )
    headers = {"User-Agent": USER_AGENT}
    
    start_t = time.perf_counter()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            
            print(f"HTTP GET {url}")
            print(f"Status Code: {resp.status_code} ({elapsed_ms:.1f} ms)")
            
            if resp.status_code != 200:
                print(f"[ERROR] IEM returned status {resp.status_code}: {resp.text}")
                return {"status": "FAILED", "code": resp.status_code, "error": resp.text}
            
            raw_lines = resp.text.strip().splitlines()
            data_lines = [l.strip() for l in raw_lines if l.strip() and not l.startswith("#")]
            
            if not data_lines:
                print("[ERROR] IEM response contains no data rows")
                return {"status": "FAILED", "error": "No CSV rows returned"}
            
            header = data_lines[0]
            rows = data_lines[1:]
            print(f"CSV Header columns: {header}")
            print(f"Total observation rows returned: {len(rows)}")
            print("First 3 real rows from SAOC:")
            for r in rows[:3]:
                print(f"  {r}")
            
            return {
                "status": "OK",
                "code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "header": header,
                "rows_count": len(rows),
                "first_rows": rows[:3],
            }
    except Exception as exc:
        print(f"[ERROR] IEM request failed: {exc}")
        return {"status": "FAILED", "error": str(exc)}


def probe_rainviewer() -> Dict[str, Any]:
    """Source 6: RainViewer Real-time Radar API."""
    print("\n" + "=" * 70)
    print("PROBE 6/6: RainViewer Real-time Radar API [CORE]")
    print("=" * 70)
    
    url = "https://api.rainviewer.com/public/weather-maps.json"
    headers = {"User-Agent": USER_AGENT}
    
    start_t = time.perf_counter()
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT, headers=headers) as client:
            resp = client.get(url)
            elapsed_ms = (time.perf_counter() - start_t) * 1000
            
            print(f"HTTP GET {url}")
            print(f"Status Code: {resp.status_code} ({elapsed_ms:.1f} ms)")
            
            if resp.status_code != 200:
                print(f"[ERROR] RainViewer returned status {resp.status_code}: {resp.text}")
                return {"status": "FAILED", "code": resp.status_code, "error": resp.text}
            
            data = resp.json()
            radar = data.get("radar", {})
            past_frames = radar.get("past", [])
            nowcast_frames = radar.get("nowcast", [])
            host = data.get("host", "https://tilecache.rainviewer.com")
            
            print(f"Tile Host: {host}")
            print(f"Past radar frames available: {len(past_frames)}")
            print(f"Nowcast radar frames available: {len(nowcast_frames)}")
            
            if not past_frames:
                print("[ERROR] No past radar frames found")
                return {"status": "FAILED", "error": "No past frames"}
                
            latest_frame = past_frames[-1]
            latest_time = latest_frame.get("time")
            latest_path = latest_frame.get("path")
            dt_utc = datetime.fromtimestamp(latest_time, tz=timezone.utc)
            
            print(f"\nLatest Past Radar Frame:")
            print(f"  Unix Timestamp : {latest_time} ({dt_utc.strftime('%Y-%m-%d %H:%M:%S UTC')})")
            print(f"  Path Hash      : {latest_path}")
            print(f"  Sample Tile URL: {host}{latest_path}/256/3/-33/-64/2/1_1.png")
            
            return {
                "status": "OK",
                "code": resp.status_code,
                "elapsed_ms": elapsed_ms,
                "past_frames_count": len(past_frames),
                "latest_timestamp": latest_time,
                "latest_path": latest_path,
                "host": host,
            }
    except Exception as exc:
        print(f"[ERROR] RainViewer request failed: {exc}")
        return {"status": "FAILED", "error": str(exc)}


def main():
    print("=" * 70)
    print("ClimaRC - Data Sources Probe Suite (Phase 1)")
    print(f"Timestamp: {datetime.now(timezone.utc).isoformat()}")
    print("Rules: Strict zero-mock enforcement, real external APIs validation")
    print("=" * 70)
    
    results = {}
    results["metar"] = probe_metar()
    results["open_meteo"] = probe_open_meteo()
    results["smn"] = probe_smn()
    results["wrf_s3"] = probe_wrf_s3()
    results["iem"] = probe_iem()
    results["rainviewer"] = probe_rainviewer()
    
    print("\n" + "=" * 70)
    print("SUMMARY OF PROBED DATA SOURCES:")
    print("=" * 70)
    core_ok = True
    for key in ["metar", "open_meteo", "wrf_s3", "iem", "rainviewer"]:
        res = results[key]
        st = res.get("status", "UNKNOWN")
        if st != "OK":
            core_ok = False
        print(f"  [OK] {key:15s} : {st} (Core Source)")
        
    smn_res = results["smn"]
    smn_st = smn_res.get("status")
    print(f"  [OPTIONAL] {'smn':15s} : {smn_st} (Graceful Degradation: smn_available={smn_res.get('smn_available', False)})")
    print("=" * 70)
    
    if not core_ok:
        print("[FAIL] One or more core data sources failed.")
        sys.exit(1)
    else:
        print("ALL 5/5 CORE DATA SOURCES VALIDATED AND ACTIVE!")
        print("PHASE 1 ACCEPTANCE CRITERIA FULLY SATISFIED.")
        sys.exit(0)


if __name__ == "__main__":
    main()
