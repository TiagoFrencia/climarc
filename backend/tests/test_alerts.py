from backend.app.services.local_alerts_detector import detect_local_alerts


def test_alert_metar_thunderstorm_and_hail():
    metar_data = {
        "icao": "SAOC",
        "raw_metar": "METAR SAOC 230000Z 18025G40KT 2000 +TSRA GR BKN020CB 22/18 Q1008",
        "wx_codes": ["+TSRA", "GR"],
        "temp_c": 22.0,
        "wind_speed_kmh": 46.0,
    }

    alerts = detect_local_alerts(
        city_key="rio_cuarto",
        metar_data=metar_data,
    )

    types = [a["type"] for a in alerts]
    levels = [a["level"] for a in alerts]

    assert "granizo" in types
    assert "tormenta" in types
    assert "naranja" in levels


def test_alert_barometric_drop():
    # History shows a 4.0 hPa pressure drop across 3 hours
    metar_history = [
        {"obs_time_utc": "2026-11-23T04:00:00Z", "qnh_hpa": 1008.0},
        {"obs_time_utc": "2026-11-23T03:00:00Z", "qnh_hpa": 1009.5},
        {"obs_time_utc": "2026-11-23T01:00:00Z", "qnh_hpa": 1012.0},  # 3 hours prior: 1012.0 -> drop of 4.0 hPa
    ]

    alerts = detect_local_alerts(
        city_key="rio_cuarto",
        metar_history_records=metar_history,
    )

    types = [a["type"] for a in alerts]
    assert "caida_barometrica" in types
    baro_alert = next(a for a in alerts if a["type"] == "caida_barometrica")
    assert baro_alert["level"] == "amarillo"


def test_alert_forecast_wind_gusts():
    om_hourly = [
        {"models": {"ecmwf_ifs025": {"wind_gust_kmh": 55.0}}},
        {"models": {"ecmwf_ifs025": {"wind_gust_kmh": 72.0}}},  # >= 70 triggers naranja
        {"models": {"ecmwf_ifs025": {"wind_gust_kmh": 40.0}}},
    ]

    alerts = detect_local_alerts(
        city_key="rio_cuarto",
        open_meteo_hourly=om_hourly,
    )

    types = [a["type"] for a in alerts]
    assert "viento_fuerte" in types
    viento_alert = next(a for a in alerts if a["type"] == "viento_fuerte")
    assert viento_alert["level"] == "naranja"


def test_alert_convective_wrf_3x3_nearby():
    # WRF 3x3 window shows severe localized cell: 10.5 mm/h
    wrf_city_data = {
        "hourly": [
            {"precip_mm": 0.0, "precip_nearby_max_mm": 10.5, "wind_nearby_max_kmh": 30.0},
            {"precip_mm": 0.0, "precip_nearby_max_mm": 5.0, "wind_nearby_max_kmh": 20.0},
        ]
    }

    alerts = detect_local_alerts(
        city_key="rio_cuarto",
        wrf_city_data=wrf_city_data,
    )

    types = [a["type"] for a in alerts]
    assert "conveccion_severa" in types
    convec_alert = next(a for a in alerts if a["type"] == "conveccion_severa")
    assert convec_alert["level"] in ["amarillo", "naranja"]
