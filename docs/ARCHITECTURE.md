# ClimaRC - Arquitectura de Sistema y Catálogo de Fuentes de Datos

## 1. Visión General del Proyecto
**ClimaRC** es una Progressive Web App (PWA) de meteorología hiperlocal de alta precisión diseñada específicamente para **Río Cuarto** y **Córdoba Capital**, Argentina. Su objetivo es unificar observaciones en tiempo real de aeródromos locales (METAR), modelos globales de alta resolución (ECMWF, GFS, ICON), el modelo regional mesoescalar WRF del Servicio Meteorológico Nacional (SMN) y corrección predictiva mediante Machine Learning liviano (LightGBM).

---

## 2. Estructura del Monorepo

```
climaRio/
├── backend/                  # API liviana FastAPI para Render Free Tier (< 150 MB RAM)
│   └── requirements.txt      # fastapi, uvicorn, httpx, pydantic, lightgbm, scikit-learn, etc.
├── scripts/                  # Trabajos científicos pesados (corren en GitHub Actions / cron)
│   ├── probe_sources.py      # Suite de validación en vivo de las fuentes reales
│   └── requirements-jobs.txt # s3fs, xarray, h5netcdf, h5py, netCDF4, etc.
├── frontend/                 # PWA cliente (Vue/React + Vite - Fase 4)
├── data/                     # Almacenamiento local para cache y wrf_latest.json
├── docs/
│   └── ARCHITECTURE.md       # Este documento técnico de referencia
└── .gitignore
```

### Reglas Críticas de Aislamiento de Memoria:
- **Render Free Tier (Límite 512 MB RAM):** El `/backend` corre en producción con una huella de memoria estrictamente inferior a 150 MB RAM.
- **Prohibición de librerías científicas en Backend:** `xarray`, `netCDF4`, `h5netcdf`, `h5py` y `s3fs` están **estrictamente prohibidas** en `/backend/requirements.txt`. El procesamiento del NetCDF de WRF y la extracción de puntos para Río Cuarto y Córdoba se delegan a GitHub Actions (`/scripts`), el cual publica un JSON procesado y compacto (`data/wrf_latest.json`).
- **Tolerancia a fallos y reinicios efímeros:** Todas las llamadas HTTP externas cuentan con timeout explícito (`15.0s`), `User-Agent: ClimaRC-PersonalApp/1.0`, y mecanismos de fallback silencioso para que la caída de una fuente externa nunca provoque HTTP 500 en la API de ClimaRC.

---

## 3. Catálogo y Especificación de las Fuentes Reales

### Fuente 1: Aviation Weather METAR (SAOC y SACO) [CORE - ACTIVA]
- **Tipo:** Observación oficial de superficie de estaciones meteorológicas de aeródromo.
- **Estaciones:**
  - `SAOC`: Aeropuerto de Río Cuarto (Las Higueras), Córdoba (-33.0855, -64.2613).
  - `SACO`: Aeropuerto Internacional Ingeniero Ambrosio Taravella, Córdoba (-31.3236, -64.2081).
- **Endpoint GET:**
  `https://aviationweather.gov/api/data/metar?ids=SAOC,SACO&format=json`
- **Headers:** `User-Agent: ClimaRC-PersonalApp/1.0`
- **Esquema Real de Respuesta (JSON):**
  ```json
  [
    {
      "metar_id": 78945612,
      "icaoId": "SAOC",
      "receiptTime": "2026-11-23 00:05:00",
      "obsTime": 1795392000,
      "reportTime": "2026-11-23 00:00:00",
      "temp": 21.0,
      "dewp": 16.0,
      "wdir": 20,
      "wspd": 7,
      "wgst": null,
      "visib": "9999",
      "altim": 1010.0,
      "rawOb": "METAR SAOC 230000Z 02007KT 9999 BKN030 21/16 Q1010"
    }
  ]
  ```
- **Unidades:**
  - `temp`: Grados Celsius (°C).
  - `dewp`: Punto de rocío en grados Celsius (°C).
  - `wdir`: Dirección del viento en grados geográficos (0-360°).
  - `wspd`: Velocidad del viento en nudos (kt). Multiplicar por 1.852 para km/h.
  - `wgst`: Ráfaga de viento en nudos (kt) o `null` si no hay ráfaga registrada.
  - `altim`: Presión a nivel de estación corregida (QNH) en hectopascales (hPa).

---

### Fuente 2: Open-Meteo Multi-Modelo [CORE - ACTIVA]
- **Tipo:** Ensamble numérico horario global calibrado.
- **Modelos solicitados simultáneamente:**
  1. `ecmwf_ifs025`: ECMWF Integrated Forecasting System (resolución 0.25° ~ 25 km, líder mundial).
  2. `gfs_seamless`: NOAA NCEP GFS operacional (resolución 13-25 km).
  3. `icon_seamless`: DWD Deutscher Wetterdienst ICON (resolución 7-13 km).
- **Endpoint GET:**
  `https://api.open-meteo.com/v1/forecast?latitude=-33.1232&longitude=-64.3493&hourly=temperature_2m,apparent_temperature,relative_humidity_2m,precipitation,precipitation_probability,wind_speed_10m,wind_gusts_10m,wind_direction_10m,surface_pressure,weather_code,cape&models=ecmwf_ifs025,gfs_seamless,icon_seamless`
- **Estructura de Salida:**
  El objeto `hourly` contiene un arreglo `time` de 168 horas y series individuales por modelo:
  - `temperature_2m_ecmwf_ifs025`, `temperature_2m_gfs_seamless`, `temperature_2m_icon_seamless` (°C)
  - `precipitation_ecmwf_ifs025`, `precipitation_gfs_seamless`, `precipitation_icon_seamless` (mm)
  - `wind_speed_10m_...` (km/h) y `wind_gusts_10m_...` (km/h)
  - `wind_direction_10m_...` (°)
  - `surface_pressure_...` (hPa)
  - `cape_...` (J/kg) -> Fundamental para pronóstico de tormentas severas y convección en el sur de Córdoba.

---

### Fuente 3: API Oficial del SMN Argentina [OPCIONAL - DEGRADACIÓN ELEGANTE]
- **Decisión de Arquitectura:**
  Para garantizar el funcionamiento 100% autónomo y sin mantenimiento manual en Render Free Tier (sin depender de tokens efímeros que expiran cada hora), la integración del SMN se diseñó bajo el patrón de **Degradación Elegante (Graceful Degradation)**:
  - Si el token JWT anónimo no está presente o está vencido, el backend responde con `smn_available: false`.
  - El sistema **nunca se bloquea ni retorna HTTP 500**. Toda la funcionalidad climática, pronóstico multi-modelo, radar y métricas de aeródromo continúan activas a través de las fuentes 1, 2, 4, 5 y 6.
- **Auditoría Técnica de Endpoints Públicos de Alertas:**
  - `https://ws.smn.gob.ar/alerts/type/AC` -> **HTTP 503 Service Unavailable** (servicio legado apagado por SMN).
  - `https://ws.smn.gob.ar/alerts/type/AL` -> **HTTP 503 Service Unavailable** (servicio legado apagado por SMN).
  - `https://ws.smn.gob.ar/map_items/weather` -> **HTTP 503 Service Unavailable** (servicio legado apagado por SMN).
  - `https://ssl.smn.gob.ar/...` -> **HTTP 403 Cloudflare Managed Challenge**.
  - `https://ws1.smn.gob.ar/v1/...` -> Requiere estrictamente `Authorization: JWT <token>`.
- **Identificadores Reales Catalogados para el SMN:**
  - **Río Cuarto:**
    - `location_id` / `smn_id`: **`6746`**
    - `smn_id_interno`: **`7007`**
    - `station_id` (Estación Río Cuarto Aero): **`4864`**
  - **Córdoba Capital:**
    - `location_id` / `smn_id`: **`6173`**
    - `smn_id_interno`: **`6425`**
    - `smn_id_observatorio`: **`10469`** (`smn_id_interno`: `10823`)

---

### Fuente 4: Modelo Regional WRF-SMN en AWS S3 (`s3://smn-ar-wrf`) [CORE - ACTIVA]
- **Tipo:** Modelo mesoescalar numérico regional operativo (WRF-ARW) del SMN de Argentina, publicado en AWS S3 público (`anon=True`).
- **Estructura del Bucket:**
  `s3://smn-ar-wrf/DATA/WRF/DET/{YYYY}/{MM}/{DD}/{HH}/`
  - Horas de corrida (`HH`): `00` y `12` UTC.
  - Archivos horarios: `WRFDETAR_01H_{YYYYMMDD}_{HH}_{FFF}.nc` donde `FFF` es la hora pronosticada (`001` a `072`).
- **Malla Espacial 2D y Estrategia de Indexación para Fase 2:**
  - Dimensiones: `time: 1, y: 1249, x: 999`
  - **Importante sobre indexación:** Las coordenadas espaciales son matrices curvilíneas 2D: `lat(y, x)` y `lon(y, x)` sobre proyección `Lambert_Conformal`.
  - **Prohibición de `.sel(lat=..., lon=...)`:** En xarray, `.sel()` solo funciona con coordenadas 1D monótonas. Para mallas 2D como WRF-SMN, la extracción de puntos para Fase 2 se realiza obligatoriamente mediante indexación matricial rápida por índices `(y_idx, x_idx)`.
  - **Índices Precalculados Exactos para ClimaRC:**
    * **Río Cuarto** (Target: lat=-33.1232, lon=-64.3493):
      - `y_idx = 676, x_idx = 514`
      - Coordenada de grilla más cercana: `lat = -33.1277°, lon = -64.3559°` (distancia < 800 m).
    * **Córdoba Capital** (Target: lat=-31.4201, lon=-64.1888):
      - `y_idx = 723, x_idx = 518`
      - Coordenada de grilla más cercana: `lat = -31.4377°, lon = -64.2003°` (distancia ~2 km).
- **Análisis y Verificación Empírica de Precipitación (`PP`):**
  - Se comparó el archivo `001.nc` frente a `002.nc` de la corrida `2026-11-23 00 UTC`:
    * Atributos: `long_name: 'Accumulated Total Precipitation'`, `units: 'millimeter'`.
    * Rango en `001.nc`: `[0.0000, 69.6471]`, media `0.0856` mm.
    * Rango en `002.nc`: `[0.0000, 55.4477]`, media `0.0754` mm.
    * Diferencia `PP(002) - PP(001)`: Rango `[-64.7661, 54.8312]`. En **284.905 puntos de grilla el valor descendió**.
    * **CONCLUSIÓN ARQUITECTÓNICA DEFINITIVA:** Cada archivo horario `WRFDETAR_01H` (`01H` = 1 Hora) contiene la **precipitación horaria discreta** caída durante esa ventana de 1 hora (mm/h), y **NO** una acumulación monotónica desde t=0. Por ende, en la Fase 2 **no se debe restar** $PP(t) - PP(t-1)$, sino utilizar directamente el valor de `PP` como precipitación horaria pronosticada.
- **Catálogo de Variables y Unidades Reales:**
  *(Nota crítica: Las temperaturas del WRF-SMN vienen calibradas en Celsius, NO en Kelvin)*
  | Variable | Dimensiones | Unidades Reales | Significado Físico |
  | :--- | :--- | :--- | :--- |
  | **`T2`** | `(time, y, x)` | **`degree_Celsius`** | Temperatura a 2m calibrada con metodología RAFK |
  | **`HR2`** | `(time, y, x)` | **`percent`** | Humedad relativa a 2 metros (0 - 100%) |
  | **`magViento10`** | `(time, y, x)` | **`meter / second`** | Intensidad de viento a 10m calibrada RAFK (m/s) |
  | **`dirViento10`** | `(time, y, x)` | **`degree`** | Dirección del viento a 10m (0 - 360°) |
  | **`PP`** | `(time, y, x)` | **`millimeter`** | Precipitación discreta del intervalo horario (mm/h) |
  | **`PSFC`** | `(time, y, x)` | **`hPa`** | Presión atmosférica en superficie |
  | **`Freezing_level`**| `(time, y, x)` | **`m`** | Altura sobre el nivel del mar de la isoterma 0°C |
  | **`TSLB`** | `(time, y, x)` | **`degree_Celsius`** | Temperatura del suelo |
  | **`SMOIS`** | `(time, y, x)` | **`m3 m-3`** | Humedad volumétrica del suelo |

---

### Fuente 5: Historial ASOS/METAR en Iowa Environmental Mesonet (IEM) [CORE - ACTIVA]
- **Tipo:** Repositorio histórico de observaciones meteorológicas de superficie.
- **Estación:** `SAOC` (Río Cuarto).
- **Endpoint GET:**
  `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=SAOC&data=tmpc&data=dwpc&data=relh&data=sknt&data=gust&data=drct&data=mslp&hours=48&tz=Etc/UTC&format=onlycomma&missing=null&report_type=3&report_type=4`
- **Esquema CSV Real Verificado:**
  ```csv
  station,valid,tmpc,dwpc,relh,sknt,gust,drct,mslp
  SAOC,2026-11-23 00:00,21.00,16.00,73.13,7.00,null,20.00,null
  SAOC,2026-11-22 23:00,22.00,16.00,68.79,7.00,null,20.00,null
  SAOC,2026-11-22 22:00,24.00,13.00,50.22,9.00,null,50.00,null
  ```
- **Campos y Formatos:**
  - `station`: Identificador ICAO de 4 letras (`SAOC`).
  - `valid`: Fecha y hora UTC (`YYYY-MM-DD HH:MM`).
  - `tmpc`: Temperatura del aire en grados Celsius (°C).
  - `dwpc`: Temperatura de rocío en grados Celsius (°C).
  - `relh`: Humedad relativa en porcentaje (%).
  - `sknt`: Velocidad del viento en nudos (kt).
  - `gust`: Ráfagas en nudos (kt) o `null`.
  - `drct`: Dirección del viento en grados (0-360°).
  - `mslp`: Presión reducida a nivel medio del mar (hPa / mb) o `null`.
- **Utilidad en ClimaRC:** Entrenamiento del modelo LightGBM para remoción de sesgos locales de temperatura y viento sobre el WRF-SMN.

---

### Fuente 6: Radar Meteorológico RainViewer [CORE - ACTIVA]
- **Tipo:** Mosaico de reflectividad doppler en tiempo real.
- **Endpoint GET:**
  `https://api.rainviewer.com/public/weather-maps.json`
- **Estructura Real de Respuesta (JSON):**
  ```json
  {
    "version": "2.0",
    "generated": 1795404612,
    "host": "https://tilecache.rainviewer.com",
    "radar": {
      "past": [
        {"time": 1795404600, "path": "/v2/radar/0697e213706f"},
        {"time": 1795405800, "path": "/v2/radar/950d9692f64a"}
      ],
      "nowcast": []
    }
  }
  ```
- **Generación de Tiles de Mapa:**
  `{host}{path}/256/{z}/{lat}/{lon}/{color_scheme}/{smooth}_{snow}.png`
  - Coordenadas de prueba para Río Cuarto: `https://tilecache.rainviewer.com/v2/radar/950d9692f64a/256/3/-33/-64/2/1_1.png`
  - Cero costo de CPU en el servidor (los tiles se renderizan directamente en la PWA cliente).
