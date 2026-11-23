"""
ClimaRC - Regional WRF-SMN Extractor
Standalone script designed to run in GitHub Actions every 6 hours or locally.
Extracts point forecasts and 3x3 convective neighborhood statistics for Río Cuarto and Córdoba.
Writes a compact JSON file (< 45 KB) to data/wrf_latest.json.

CRITICAL MEMORY RULE:
This script and requirements-jobs.txt are the ONLY places where xarray, s3fs,
h5netcdf, netCDF4, and h5py are used.
"""

import os
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, List

import s3fs
import xarray as xr
import numpy as np

# Coordinates and precalculated 2D mesh grid indices from Phase 1
CITIES = {
    "rio_cuarto": {
        "name": "Río Cuarto",
        "lat": -33.1232,
        "lon": -64.3493,
        "grid_y": 676,
        "grid_x": 514,
        "icao": "SAOC",
    },
    "cordoba": {
        "name": "Córdoba Capital",
        "lat": -31.4201,
        "lon": -64.1888,
        "grid_y": 723,
        "grid_x": 518,
        "icao": "SACO",
    },
}

BUCKET_PREFIX = "smn-ar-wrf/DATA/WRF/DET"


def find_latest_cycle(fs: s3fs.S3FileSystem) -> Tuple[datetime, str]:
    """
    Locates the most recent operational cycle (00 or 12 UTC) available on S3.
    Searches backwards from the current UTC date up to 4 days.
    """
    now = datetime.now(timezone.utc)
    candidate_dates = [now - timedelta(days=d) for d in range(5)]

    for dt in candidate_dates:
        yyyy = dt.strftime("%Y")
        mm = dt.strftime("%m")
        dd = dt.strftime("%d")
        for hh in ["12", "00"]:
            prefix = f"{BUCKET_PREFIX}/{yyyy}/{mm}/{dd}/{hh}/"
            test_file = f"{prefix}WRFDETAR_01H_{yyyy}{mm}{dd}_{hh}_001.nc"
            try:
                if fs.exists(test_file):
                    cycle_dt = datetime(int(yyyy), int(mm), int(dd), int(hh), 0, 0, tzinfo=timezone.utc)
                    return cycle_dt, prefix
            except Exception:
                pass

    raise RuntimeError(f"Could not find any recent WRF cycle in s3://{BUCKET_PREFIX}")


def extract_wrf_hourly(
    fs: s3fs.S3FileSystem,
    cycle_dt: datetime,
    prefix: str,
    max_hours: int = 48
) -> Dict[str, Any]:
    """
    Extracts hourly forecast series for all configured cities from lead hour 001 to max_hours.
    """
    yyyy = cycle_dt.strftime("%Y")
    mm = cycle_dt.strftime("%m")
    dd = cycle_dt.strftime("%d")
    hh = cycle_dt.strftime("%H")
    cycle_str = f"{yyyy}{mm}{dd}_{hh}"

    city_results: Dict[str, List[Dict[str, Any]]] = {k: [] for k in CITIES}
    successful_hours = 0

    print(f"Extracting WRF cycle {cycle_str} (up to {max_hours} hours)...")

    for hour in range(1, max_hours + 1):
        filename = f"WRFDETAR_01H_{cycle_str}_{hour:03d}.nc"
        file_path = f"{prefix}{filename}"
        timestamp_utc = (cycle_dt + timedelta(hours=hour)).strftime("%Y-%m-%dT%H:%M:%SZ")

        t0 = time.perf_counter()
        stream = None
        ds = None
        try:
            stream = fs.open(file_path, "rb")
            ds = xr.open_dataset(stream, engine="h5netcdf")

            for city_key, city_info in CITIES.items():
                cy = city_info["grid_y"]
                cx = city_info["grid_x"]

                # 3x3 convective neighborhood slice (~12x12 km window)
                y_slice = slice(cy - 1, cy + 2)
                x_slice = slice(cx - 1, cx + 2)

                t2_slice = ds["T2"].isel(time=0, y=y_slice, x=x_slice).values
                hr2_slice = ds["HR2"].isel(time=0, y=y_slice, x=x_slice).values
                wspd_slice = ds["magViento10"].isel(time=0, y=y_slice, x=x_slice).values
                wdir_slice = ds["dirViento10"].isel(time=0, y=y_slice, x=x_slice).values
                pp_slice = ds["PP"].isel(time=0, y=y_slice, x=x_slice).values
                psfc_slice = ds["PSFC"].isel(time=0, y=y_slice, x=x_slice).values

                # Center point is index [1, 1] of the 3x3 slice
                # Note: T2 in WRF-SMN is already in Celsius (RAFK calibrated), DO NOT subtract 273.15!
                # PP is discrete 1h precipitation in mm/h, DO NOT difference!
                # magViento10 is in m/s, multiply by 3.6 for km/h
                t2_c = round(float(t2_slice[1, 1]), 1)
                hr2_pct = round(max(0.0, min(100.0, float(hr2_slice[1, 1]))), 1)
                wspd_kmh = round(float(wspd_slice[1, 1]) * 3.6, 1)
                wdir_deg = round(float(wdir_slice[1, 1]) % 360.0, 0)
                pp_mm = round(max(0.0, float(pp_slice[1, 1])), 2)
                psfc_hpa = round(float(psfc_slice[1, 1]), 1)

                # 3x3 neighborhood maximums for convective and severe wind warning
                precip_nearby_max_mm = round(max(0.0, float(np.max(pp_slice))), 2)
                wind_nearby_max_kmh = round(float(np.max(wspd_slice)) * 3.6, 1)

                record = {
                    "lead_hour": hour,
                    "timestamp_utc": timestamp_utc,
                    "temperature_c": t2_c,
                    "relative_humidity_pct": hr2_pct,
                    "wind_speed_kmh": wspd_kmh,
                    "wind_direction_deg": wdir_deg,
                    "wind_nearby_max_kmh": wind_nearby_max_kmh,
                    "precip_mm": pp_mm,
                    "precip_nearby_max_mm": precip_nearby_max_mm,
                    "surface_pressure_hpa": psfc_hpa,
                }
                city_results[city_key].append(record)

            successful_hours += 1
            elapsed_s = time.perf_counter() - t0
            if hour % 6 == 0 or hour == 1 or hour == max_hours:
                print(f"  Hour {hour:02d}/{max_hours:02d} processed in {elapsed_s:.2f}s (valid {timestamp_utc})")

        except Exception as exc:
            print(f"  [WARN] Failed processing hour {hour:03d} ({file_path}): {exc}")
        finally:
            if ds is not None:
                try:
                    ds.close()
                except Exception:
                    pass
            if stream is not None:
                try:
                    stream.close()
                except Exception:
                    pass

    output_cities = {}
    for city_key, city_info in CITIES.items():
        output_cities[city_key] = {
            "name": city_info["name"],
            "lat": city_info["lat"],
            "lon": city_info["lon"],
            "icao": city_info["icao"],
            "grid_y": city_info["grid_y"],
            "grid_x": city_info["grid_x"],
            "hourly": city_results[city_key],
        }

    payload = {
        "metadata": {
            "source": "SMN-AR-WRF-DET",
            "run_time_utc": cycle_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "hours_count": successful_hours,
            "max_hours_requested": max_hours,
        },
        "cities": output_cities,
    }

    return payload


def main():
    parser = argparse.ArgumentParser(description="Fetch and extract point forecasts from SMN-AR-WRF on AWS S3.")
    parser.add_argument(
        "--max-hours",
        type=int,
        default=48,
        help="Maximum lead hours to extract (default: 48, max: 72)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/wrf_latest.json",
        help="Target output path for the JSON payload (default: data/wrf_latest.json)",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("ClimaRC - SMN Regional WRF Extractor")
    print("=" * 60)
    print(f"Requested max hours: {args.max_hours}")
    print(f"Target output file:  {args.output}")

    fs = s3fs.S3FileSystem(anon=True)
    cycle_dt, prefix = find_latest_cycle(fs)
    print(f"Located latest WRF cycle: {cycle_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"Prefix: s3://{prefix}")

    payload = extract_wrf_hourly(fs, cycle_dt, prefix, max_hours=args.max_hours)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))

    file_size_kb = os.path.getsize(args.output) / 1024.0
    print("-" * 60)
    print(f"Wrote {payload['metadata']['hours_count']} hourly records to {args.output}")
    print(f"File size: {file_size_kb:.2f} KB (Compact target < 45 KB)")
    print("WRF extraction completed successfully.")


if __name__ == "__main__":
    main()
