FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Instalar dependencias de sistema minimas:
# - libgomp1: OpenMP runtime requerido por LightGBM
# - tzdata: zona horaria America/Argentina/Cordoba para ZoneInfo
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copiar e instalar dependencias de Python primero para aprovechar cache de Docker
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copiar codigo fuente del backend y modelos entrenados (.joblib y metrics.json)
COPY backend /app/backend

# Crear directorio para base de datos SQLite y cache de datos
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["sh", "-c", "uvicorn backend.app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
