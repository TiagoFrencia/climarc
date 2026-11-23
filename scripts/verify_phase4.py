import urllib.request
import json
import sys

def main():
    print("1. Testing frontend root at http://127.0.0.1:5173/")
    req_front = urllib.request.Request("http://127.0.0.1:5173/")
    with urllib.request.urlopen(req_front) as resp:
        html = resp.read().decode("utf-8")
        assert resp.status == 200
        assert "ClimaRC" in html
        print("   -> Frontend served 200 OK, HTML loaded successfully!")

    print("2. Testing CORS on backend http://127.0.0.1:8000/api/weather")
    req_cors = urllib.request.Request(
        "http://127.0.0.1:8000/api/weather?city=rio_cuarto",
        headers={"Origin": "http://localhost:5173"}
    )
    with urllib.request.urlopen(req_cors) as resp:
        assert resp.status == 200
        cors_hdr = resp.headers.get("access-control-allow-origin")
        data = json.loads(resp.read().decode("utf-8"))
        print(f"   -> Backend CORS allow-origin: {cors_hdr}")
        assert cors_hdr == "http://localhost:5173"
        print(f"   -> City: {data['city']['name']}, Temp: {data['current']['temperature_c']} °C, ML applied: {data['current']['ml_applied']}")

    print("3. Testing Vite dev proxy at http://127.0.0.1:5173/api/weather?city=rio_cuarto")
    req_proxy = urllib.request.Request("http://127.0.0.1:5173/api/weather?city=rio_cuarto")
    with urllib.request.urlopen(req_proxy) as resp:
        assert resp.status == 200
        data_proxy = json.loads(resp.read().decode("utf-8"))
        print(f"   -> Vite proxy OK! Weather city: {data_proxy['city']['name']}")

    print("4. Testing Radar at http://127.0.0.1:8000/api/radar")
    req_radar = urllib.request.Request(
        "http://127.0.0.1:8000/api/radar",
        headers={"Origin": "http://localhost:5173"}
    )
    with urllib.request.urlopen(req_radar) as resp:
        assert resp.status == 200
        radar = json.loads(resp.read().decode("utf-8"))
        past_frames = len(radar.get("radar", {}).get("past", []))
        print(f"   -> Radar OK! Host: {radar.get('host')}, Past frames count: {past_frames}")

    print("5. Testing Accuracy at http://127.0.0.1:8000/api/accuracy?city=rio_cuarto")
    req_acc = urllib.request.Request(
        "http://127.0.0.1:8000/api/accuracy?city=rio_cuarto",
        headers={"Origin": "http://localhost:5173"}
    )
    with urllib.request.urlopen(req_acc) as resp:
        assert resp.status == 200
        acc = json.loads(resp.read().decode("utf-8"))
        print(f"   -> Accuracy OK! Station: {acc['icao']}, Samples: {acc['samples_count']}, MAE Temp: {acc['mae_temperature_c']} °C")

    print("\n==========================================")
    print("ALL LIVE END-TO-END VERIFICATIONS PASSED!")
    print("==========================================")

if __name__ == "__main__":
    main()
