import React, { useState, useEffect, useCallback } from 'react';
import { Header } from './components/Header';
import { AlertsBanner } from './components/AlertsBanner';
import { HeroCurrent } from './components/HeroCurrent';
import { HourlyStrip } from './components/HourlyStrip';
import { MobilityCard } from './components/MobilityCard';
import { RadarMap } from './components/RadarMap';
import { DailyForecastList } from './components/DailyForecastList';
import { ModelsTransparency } from './components/ModelsTransparency';
import { AccuracyModal } from './components/AccuracyModal';
import { SettingsModal } from './components/SettingsModal';
import type { WeatherPayload } from './types/weather';
import { fetchWeather } from './services/api';
import { AlertCircle, RefreshCw } from 'lucide-react';

interface CachedWeatherEntry {
  timestamp: number;
  data: WeatherPayload;
}

function getStorageKey(cityKey: string, gpsActive: boolean, customCoords: { lat: number; lon: number } | null): string {
  if (gpsActive && customCoords) {
    return `climarc_snap_gps_${customCoords.lat.toFixed(2)}_${customCoords.lon.toFixed(2)}`;
  }
  return `climarc_snap_${cityKey}`;
}

function readCachedWeather(key: string): { data: WeatherPayload; ageMinutes: number } | null {
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return null;
    const entry: CachedWeatherEntry = JSON.parse(raw);
    const ageMs = Date.now() - entry.timestamp;
    const maxAgeMs = 6 * 60 * 60 * 1000; // 6 hours
    if (ageMs > maxAgeMs) return null;
    const ageMinutes = Math.max(0, Math.round(ageMs / (60 * 1000)));
    return { data: entry.data, ageMinutes };
  } catch {
    return null;
  }
}

function writeCachedWeather(key: string, data: WeatherPayload): void {
  try {
    const entry: CachedWeatherEntry = {
      timestamp: Date.now(),
      data,
    };
    localStorage.setItem(key, JSON.stringify(entry));
  } catch (e) {
    console.warn('Could not write cache to localStorage:', e);
  }
}

export const App: React.FC = () => {
  const [cityKey, setCityKey] = useState<string>('rio_cuarto');
  const [customCoords, setCustomCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [gpsActive, setGpsActive] = useState<boolean>(false);

  // Synchronous read of default city cache on initial mount for 0 ms instant load
  const initialCache = readCachedWeather('climarc_snap_rio_cuarto');
  const [weatherData, setWeatherData] = useState<WeatherPayload | null>(() => initialCache?.data || null);
  const [cachedAge, setCachedAge] = useState<number | null>(() => initialCache?.ageMinutes ?? null);
  const [isBackgroundRevalidating, setIsBackgroundRevalidating] = useState<boolean>(false);
  const [isLoading, setIsLoading] = useState<boolean>(() => !initialCache);
  const [error, setError] = useState<string | null>(null);

  const [isAccuracyOpen, setIsAccuracyOpen] = useState<boolean>(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);

  const loadForecast = useCallback(
    async (refresh: boolean = false) => {
      const currentStorageKey = getStorageKey(cityKey, gpsActive, customCoords);

      try {
        if (!weatherData) {
          setIsLoading(true);
        } else {
          setIsBackgroundRevalidating(true);
        }
        setError(null);

        const params: { city?: string; lat?: number; lon?: number; refresh?: boolean } = {
          refresh,
        };

        if (gpsActive && customCoords) {
          params.lat = customCoords.lat;
          params.lon = customCoords.lon;
        } else {
          params.city = cityKey;
        }

        const freshData = await fetchWeather(params);
        setWeatherData(freshData);
        writeCachedWeather(currentStorageKey, freshData);
        setCachedAge(null);
      } catch (err: any) {
        console.error('Failed to load weather:', err);
        // Only display block error if we have no stale data to show
        if (!weatherData) {
          setError(err.message || 'No se pudo conectar con el servidor meteorológico.');
        }
      } finally {
        setIsLoading(false);
        setIsBackgroundRevalidating(false);
      }
    },
    [cityKey, gpsActive, customCoords, weatherData]
  );

  // Initial load and whenever city/GPS changes
  useEffect(() => {
    loadForecast(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cityKey, gpsActive, customCoords]);

  // City selection handler with instant cache switch
  const handleSelectCity = (selectedKey: string) => {
    setGpsActive(false);
    setCustomCoords(null);
    setCityKey(selectedKey);

    const snap = readCachedWeather(`climarc_snap_${selectedKey}`);
    if (snap) {
      setWeatherData(snap.data);
      setCachedAge(snap.ageMinutes);
      setIsLoading(false);
    } else {
      setWeatherData(null);
      setCachedAge(null);
      setIsLoading(true);
    }
  };

  // GPS navigator geolocation handler
  const handleUseGps = () => {
    if (!navigator.geolocation) {
      alert('Tu navegador o dispositivo no soporta geolocalización GPS.');
      return;
    }

    setIsLoading(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const coords = {
          lat: position.coords.latitude,
          lon: position.coords.longitude,
        };
        setCustomCoords(coords);
        setGpsActive(true);

        const snap = readCachedWeather(getStorageKey(cityKey, true, coords));
        if (snap) {
          setWeatherData(snap.data);
          setCachedAge(snap.ageMinutes);
          setIsLoading(false);
        }
      },
      (err) => {
        console.warn('Geolocation error:', err);
        alert('No se pudo obtener la ubicación GPS. Verifica los permisos de tu navegador.');
        setIsLoading(false);
      },
      { timeout: 10000, enableHighAccuracy: true }
    );
  };

  const handleRefresh = () => {
    loadForecast(true);
  };

  return (
    <div className="min-h-screen bg-[#0B1120] text-slate-100 flex flex-col font-sans pb-10">
      {/* Sticky Top Header */}
      <Header
        currentCity={cityKey}
        cityConfig={weatherData?.city}
        currentWeather={weatherData?.current}
        isLoading={isLoading || isBackgroundRevalidating}
        onSelectCity={handleSelectCity}
        onUseGps={handleUseGps}
        onRefresh={handleRefresh}
        onOpenAccuracy={() => setIsAccuracyOpen(true)}
        onOpenSettings={() => setIsSettingsOpen(true)}
        gpsActive={gpsActive}
      />

      {/* Main Content Area */}
      <main className="flex-1 w-full flex flex-col items-center">
        {/* Instant-Load 0ms SWR Pill */}
        {cachedAge !== null && isBackgroundRevalidating && (
          <div className="w-full max-w-md px-4 pt-2">
            <div className="flex items-center justify-center gap-2 py-1 px-3 rounded-full bg-slate-900/90 border border-sky-500/30 text-[11px] text-sky-300 shadow-md backdrop-blur animate-pulse">
              <RefreshCw className="w-3 h-3 animate-spin text-sky-400" />
              <span>
                Datos de hace {cachedAge === 0 ? 'instantes' : `${cachedAge} min`} · Actualizando en vivo...
              </span>
            </div>
          </div>
        )}

        {error && (
          <div className="p-4 max-w-md w-full">
            <div className="p-4 rounded-2xl bg-rose-950/80 border border-rose-800 text-rose-200 flex items-start gap-3 shadow-lg">
              <AlertCircle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
              <div className="flex-1 text-xs space-y-1">
                <div className="font-bold text-sm">Error de conexión</div>
                <p>{error}</p>
                <button
                  onClick={() => loadForecast(true)}
                  className="mt-2 px-3 py-1 bg-rose-900/60 hover:bg-rose-800 rounded-lg font-semibold text-rose-100 flex items-center gap-1.5"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  <span>Reintentar</span>
                </button>
              </div>
            </div>
          </div>
        )}

        {weatherData && (
          <>
            {/* 1. Severe Weather Alerts Banner (top priority) */}
            <AlertsBanner alerts={weatherData.alerts} />

            {/* 2. Hero Card: Current condition, key metrics, executive summary, anti-hail chip */}
            <HeroCurrent
              current={weatherData.current}
              firstHour={weatherData.hourly[0]}
              nextHour={weatherData.hourly[1]}
              city={weatherData.city}
              summaryOneLiner={weatherData.summary_one_liner}
              hailRisk={weatherData.hail_risk}
            />

            {/* 3. Hourly Forecast Strip (48h Cards or 24h SVG Chart with uncertainty band) */}
            <HourlyStrip hourly={weatherData.hourly} />

            {/* 4. Mobility Card (Calle / Moto & Chequear Ruta) */}
            <MobilityCard
              current={weatherData.current}
              hourly={weatherData.hourly}
              city={weatherData.city}
              sentinels={weatherData.sentinels}
            />

            {/* 5. Live GOES-19 IR + RainViewer Radar Map Component */}
            <RadarMap
              lat={weatherData.city.lat}
              lon={weatherData.city.lon}
              cityName={weatherData.city.name}
              sentinels={weatherData.sentinels}
            />

            {/* 6. 7-Day Daily Forecast List */}
            <DailyForecastList daily={weatherData.daily} />

            {/* 7. Multi-Model & ML Bias Transparency Accordion */}
            <ModelsTransparency
              hourly={weatherData.hourly}
              mlMetadata={weatherData.ml_metadata}
              wrfAvailable={weatherData.wrf_available}
            />
          </>
        )}

        {/* Loading Skeleton during cold initial load without cache */}
        {isLoading && !weatherData && (
          <div className="flex flex-col items-center justify-center py-24 space-y-3">
            <RefreshCw className="w-8 h-8 text-sky-400 animate-spin" />
            <p className="text-sm font-medium text-slate-400">
              Cargando modelos meteorológicos y Nowcasting...
            </p>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="mt-8 text-center text-xs text-slate-400 space-y-1 max-w-md mx-auto px-4">
        <p className="font-semibold text-slate-300">
          ClimaRC · Río Cuarto & Córdoba
        </p>
        <p className="text-[11px]">
          Ensamble numérico (WRF-SMN 4km, ECMWF, ICON, GFS) + Corrección LightGBM SAOC + Nowcasting Radar RainViewer, Satélite GOES-19 IR y Rayos GLM.
        </p>
      </footer>

      {/* Modals */}
      <AccuracyModal
        isOpen={isAccuracyOpen}
        onClose={() => setIsAccuracyOpen(false)}
        cityKey={weatherData?.city.key || cityKey}
      />

      <SettingsModal
        isOpen={isSettingsOpen}
        onClose={() => setIsSettingsOpen(false)}
      />
    </div>
  );
};

export default App;
