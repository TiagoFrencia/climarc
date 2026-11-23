# 🌤️ ClimaRC — Estación Meteorológica & Ensamble Hiperlocal (Río Cuarto)

**ClimaRC** es una Progressive Web App (PWA) meteorológica de alta precisión y costo **$0/mes**, diseñada específicamente para la micro-región de **Río Cuarto (Córdoba, Argentina)** y alrededores. Combina observaciones aeronáuticas en tiempo real (METAR), modelos globales de última generación (ECMWF, ICON, GFS), extracción directa del modelo numérico de mesoescala **WRF-SMN 4km** del Servicio Meteorológico Nacional argentino, y un **corrector de sesgo local basado en Machine Learning (LightGBM)** entrenado con más de 15.000 horas de datos reales de la estación meteorológica SAOC.

---

## 🏛️ Arquitectura de Datos y Fuentes Meteorológicas

```
                           ┌──────────────────────────────┐
                           │   METAR SAOC / SACO Oficial   │──► Tiempo real, ráfagas, QNH, +TSRA/GR
                           └──────────────┬───────────────┘
                                          │
 ┌─────────────────────────────┐          │          ┌─────────────────────────────┐
 │       ECMWF / ICON / GFS    │          ▼          │     WRF-SMN 4km (AWS S3)    │
 │   (Open-Meteo Global APIs)  │──► [Nowcasting ML] ◄──│   (GitHub Actions Ingest)   │
 └──────────────┬──────────────┘          ▲          └──────────────┬──────────────┘
                │                         │                         │
                └─────────────────► [Ensemble Engine] ◄──────────────┘
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │  FastAPI Backend    │──► Telegram Bot (Alertas Proactivas)
                               │  (Render Free Tier) │──► SQLite Cache & Estado
                               └──────────┬──────────┘
                                          │
                                          ▼
                               ┌─────────────────────┐
                               │  React + Vite PWA   │──► Radar RainViewer en Vivo
                               │  (Vercel Free Tier) │──► Instalable en Android / iOS
                               └─────────────────────┘
```

1. **METAR SAOC (Aeropuerto Río Cuarto) & SACO (Córdoba Capital):**
   - Observaciones oficiales emitidas cada hora por el Servicio Meteorológico Nacional.
   - Provee temperatura real, punto de rocío, viento medio, ráfagas extremas, presión barométrica estandarizada a nivel del mar (QNH) y códigos aeronáuticos críticos de tiempo presente (`+TSRA` tormenta fuerte, `GR` granizo, `SQ` turbonada).
2. **WRF-SMN 4km (Stencil 3x3 de Alta Resolución):**
   - Modelo numérico de mesoescala oficial del SMN ejecutado con grilla de 4 km.
   - Extraído automáticamente cada 6 horas desde los buckets públicos AWS S3 mediante GitHub Actions (utilizando la memoria RAM de 7 GB de los runners para procesar archivos NetCDF pesados sin costo para el servidor de producción).
3. **Modelos Globales Ensamble:**
   - **ECMWF IFS 0.25°** (Centro Europeo, máxima precisión sinóptica).
   - **ICON** (Servicio Alemán DWD, excelente física de capas bajas).
   - **GFS** (NOAA, respaldo probabilístico global).
4. **Corrector de Sesgo ML (LightGBM):**
   - Dos modelos de Gradient Boosting (`riocuarto_temp_bias.joblib` y `riocuarto_wind_bias.joblib`) entrenados con 15.573 observaciones históricas de SAOC.
   - Corrige activamente la sobrestimación de temperatura y subestimación de viento características de la topografía pampeana-serrana.
5. **Radar Meteorológico en Vivo (RainViewer):**
   - Mosaico de reflectividad en tiempo real sincronizado con animación de capas Leaflet para el seguimiento visual de tormentas convectivas.

---

## 🚀 Ejecución en Desarrollo Local

### Opción A: Modo Nativo (Python + Node.js)

1. **Configurar el entorno virtual del backend:**
   ```bash
   python -m venv .venv
   # En Windows:
   .venv\Scripts\activate
   # En Linux/macOS:
   source .venv/bin/activate

   pip install -r backend/requirements.txt
   ```

2. **Iniciar el backend FastAPI:**
   ```bash
   uvicorn backend.app.main:app --reload --port 8000
   ```
   *Verificar salud en:* `http://localhost:8000/api/ping`

3. **Iniciar el frontend React + Vite:**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   *Acceder a la aplicación en:* `http://localhost:5173`

---

### Opción B: Con Docker Compose (Un solo comando)

Requiere tener Docker Desktop instalado y en ejecución:

```bash
docker compose up --build
```

- **Frontend (Nginx con Proxy API):** `http://localhost:3000`
- **Backend (FastAPI):** `http://localhost:8000`
- Los datos de SQLite y pronósticos se persisten localmente en `./data`.

---

## 🤖 Configuración del Bot de Alertas en Telegram

Las alertas meteorológicas proactivas (granizo, ráfagas severas, lluvia inminente, caída abrupta de temperatura) se despachan directamente a tu celular por Telegram sin costo alguno.

1. **Crear tu Bot en Telegram:**
   - Abre Telegram y busca a **`@BotFather`**.
   - Envía el comando `/newbot`.
   - Asígnale un nombre (ej: `Clima Río Cuarto`) y un nombre de usuario único terminado en `bot` (ej: `climarc_mi_bot`).
   - Copia el **HTTP API Token** proporcionado (ej: `123456789:ABCdefGhIJKlmNoPQRsTUVwxyZ`). Ese es tu `TELEGRAM_BOT_TOKEN`.

2. **Obtener tu Chat ID Personal:**
   - Busca en Telegram a **`@userinfobot`** y presiona `Iniciar` (o envía `/start`).
   - El bot te responderá con tu **Id** numérico (ej: `987654321`). Ese es tu `TELEGRAM_CHAT_ID`.

3. **Iniciar conversación con tu nuevo bot:**
   - Busca tu propio bot en Telegram y haz clic en **`Iniciar`** (o envía un `/start`). Esto es imprescindible para que Telegram permita al bot enviarte mensajes.

---

## 📦 Despliegue en la Nube 100% Gratis ($0/mes)

### 1. Subir a GitHub y Activar Actions

1. Crea un repositorio en tu cuenta de GitHub (puede ser **Público** o **Privado**).
2. Sube el código fuente:
   ```bash
   git remote add origin https://github.com/<TU_USUARIO>/<TU_REPO>.git
   git branch -M master
   git push -u origin master
   ```
3. **Activar permisos de escritura en GitHub Actions:**
   - Ve a tu repositorio en GitHub: **Settings -> Actions -> General**.
   - En la sección **Workflow permissions**, selecciona **Read and write permissions**.
   - Haz clic en **Save**. *(Esto permite al workflow `wrf_ingest.yml` publicar el pronóstico procesado en la rama `wrf-data`)*.

---

### 2. Desplegar el Backend en Render (Free Tier)

1. Inicia sesión en [render.com](https://render.com) vinculando tu cuenta de GitHub.
2. Haz clic en **New +** -> **Blueprint**.
3. Selecciona tu repositorio `climaRio`. Render detectará automáticamente el archivo `render.yaml`.
   *(O si prefieres crearlo manualmente: New -> Web Service -> Build and deploy from a Git repository -> Runtime: Docker -> Dockerfile Path: `./backend/Dockerfile`)*.
4. Completa las siguientes variables de entorno en Render:
   - `TELEGRAM_BOT_TOKEN`: El token obtenido de `@BotFather`.
   - `TELEGRAM_CHAT_ID`: Tu ID numérico de `@userinfobot`.
   - `CRON_SECRET`: Una clave segura elegida por ti (ej: `mi-clave-super-secreta-2026`).
   - `ADMIN_PIN`: Un PIN numérico para guardar cambios de umbrales desde la UI (ej: `1234`).
   - `FRONTEND_URL`: `https://climarc.vercel.app` (o el subdominio que te asigne Vercel).
   - `ALLOWED_ORIGINS`: `https://climarc.vercel.app,http://localhost:5173,http://localhost:3000`
   - `WRF_JSON_URL`: `https://raw.githubusercontent.com/<TU_USUARIO>/<TU_REPO>/wrf-data/wrf_latest.json`
5. Haz clic en **Apply / Create Web Service**. Render compilará el contenedor Docker y te otorgará una URL pública (ej: `https://climarc-api.onrender.com`).

---

### 3. Configurar Secrets en GitHub Actions

Una vez que tengas la URL de Render, ve a tu repositorio de GitHub:
- **Settings -> Secrets and variables -> Actions -> New repository secret**
- Agrega los siguientes 2 secretos:
  1. `BACKEND_URL`: La URL de tu backend en Render (ej: `https://climarc-api.onrender.com`).
  2. `CRON_SECRET`: La misma clave secreta configurada en Render.

Con esto, el workflow `.github/workflows/alerts_check.yml` se ejecutará automáticamente cada 30 minutos, despertando el backend en `/api/ping` y evaluando alertas meteorológicas en `/api/alerts/check`.

---

### 4. Desplegar el Frontend en Vercel (Free Tier)

1. Inicia sesión en [vercel.com](https://vercel.com) vinculando tu cuenta de GitHub.
2. Haz clic en **Add New... -> Project** e importa el repositorio de ClimaRC.
3. En la pantalla de configuración:
   - **Root Directory**: Haz clic en *Edit* y selecciona la carpeta **`frontend`**.
   - **Framework Preset**: **Vite** (detectado automáticamente).
   - **Environment Variables**:
     - Nombre: `VITE_API_URL`
     - Valor: La URL de Render de tu backend (ej: `https://climarc-api.onrender.com` **sin barra final**).
4. Haz clic en **Deploy**. En menos de 1 minuto tendrás tu SPA disponible en producción.

---

## 💡 Nota Práctica de Costos $0/mes: Repositorios Públicos vs. Privados

- **Si tu repositorio en GitHub es PÚBLICO:**
  - Los minutos de ejecución de GitHub Actions son **100% gratuitos e ilimitados**. Los dos workflows (`wrf_ingest` cada 6 hs y `alerts_check` cada 30 min) correrán sin restricción alguna de minutos.
- **Si tu repositorio en GitHub es PRIVADO:**
  - GitHub otorga **2.000 minutos gratis/mes** en cuentas Free.
  - El workflow `wrf_ingest.yml` (4 veces al día, ~2 min cada corrida) consume apenas ~240 min/mes.
  - El workflow `alerts_check.yml` ejecutándose cada 30 minutos consume aproximadamente ~1.440 min/mes (dentro del cupo).
  - *Alternativa gratuita de alta frecuencia (sin gastar minutos de Actions):* Puedes usar el servicio gratuito [cron-job.org](https://cron-job.org) para consultar tu backend cada 15 o 20 minutos:
    - **URL:** `https://climarc-api.onrender.com/api/alerts/check`
    - **Method:** `POST`
    - **Headers:** `X-CRON-SECRET: <TU_CRON_SECRET>`
    - Esto además mantiene caliente la instancia gratuita de Render sin consumir tu cuota de GitHub Actions.

---

## 📱 Cómo Instalar ClimaRC en tu Celular Android (PWA)

ClimaRC está configurada como una Progressive Web App (PWA) con Service Worker offline y manifiesto web:

1. Abre **Google Chrome** en tu teléfono Android y navega a la URL de Vercel de tu aplicación (ej: `https://climarc.vercel.app`).
2. Toca el menú de tres puntos verticales (`⋮`) en la esquina superior derecha del navegador.
3. Selecciona **"Agregar a la pantalla principal"** o **"Instalar aplicación"**.
4. Confirma la instalación.
5. ¡Listo! ClimaRC se instalará como una aplicación nativa en tu cajón de aplicaciones y pantalla de inicio, con pantalla completa (sin barra de direcciones), ícono representativo y rendimiento instantáneo.

---

## 🧪 Verificación y Suite de Tests

Para ejecutar la suite completa de pruebas unitarias y de integración del backend:

```bash
# Correr los 31 tests del backend
pytest backend/tests

# Validar el build de producción del frontend
cd frontend
npm run build
```
