import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from fastapi.testclient import TestClient
from backend.app.main import app
import json

client = TestClient(app)
r = client.get('/api/weather?city=rio_cuarto&refresh=true')
print('Weather endpoint status:', r.status_code)
d = r.json()
print('One-liner summary:', d.get('summary_one_liner'))
print('Sentinels stations count:', len(d.get('sentinels', [])))
for s in d.get('sentinels', []):
    print(f"  [{s['icao']}] {s['name']}: {s['distance_km']}km ({s['cardinal']}), {s['temp_c']}°C, wind={s['wind_speed_kmh']}km/h, wx={s['wx_codes']}")

print('Sentinel front detected:', d.get('sentinel_front', {}).get('detected'))
hail = d.get('hail_risk', {})
print(f"Hail risk: level={hail.get('level')}, score={hail.get('score')}, peak_window={hail.get('peak_window_local')}")
print(f"  CAPE: {hail.get('cape_max')} J/kg, Shear 0-6km: {hail.get('shear_0_6km_kmh')} km/h, Freezing level: {hail.get('freezing_level_m')} m")
print(f"  GOES-19 severe core: {hail.get('goes_severe_core_detected')}, Sentinel hail: {hail.get('sentinel_hail_detected')}")
print(f"  Explanation: {hail.get('explanation')}")

curr = d.get('current', {})
print(f"QNH trend: {curr.get('qnh_trend')} (delta: {curr.get('qnh_delta_3h_hpa')} hPa)")
print(f"UV index: {curr.get('uv_index')}")
print(f"Sunrise: {curr.get('sunrise_local')}, Sunset: {curr.get('sunset_local')}")
print(f"Models active: {d.get('models_active')}")

r_rad = client.get('/api/radar')
print('\nRadar endpoint status:', r_rad.status_code)
d_rad = r_rad.json()
if 'lightning' in d_rad:
    print(f"Lightning: provider={d_rad['lightning']['provider']}, product={d_rad['lightning']['product']}, latest={d_rad['lightning']['latest_time']}")
