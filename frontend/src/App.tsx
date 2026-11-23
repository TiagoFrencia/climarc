import React, { useState, useEffect, useCallback } from 'react';
import { Header } from './components/Header';
import { AlertsBanner } from './components/AlertsBanner';
import { HeroCurrent } from './components/HeroCurrent';
import { HourlyStrip } from './components/HourlyStrip';
import { RadarMap } from './components/RadarMap';
import { DailyForecastList } from './components/DailyForecastList';
import { ModelsTransparency } from './components/ModelsTransparency';
import { AccuracyModal } from './components/AccuracyModal';
import { SettingsModal } from './components/SettingsModal';
import type { WeatherPayload } from './types/weather';
import { fetchWeather } from './services/api';
import { AlertCircle, RefreshCw } from 'lucide-react';

export const App: React.FC = () => {
  const [weatherData, setWeatherData] = useState<WeatherPayload | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);
  const [cityKey, setCityKey] = useState<string>('rio_cuarto');
  const [customCoords, setCustomCoords] = useState<{ lat: number; lon: number } | null>(null);
  const [gpsActive, setGpsActive] = useState<boolean>(false);

  const [isAccuracyOpen, setIsAccuracyOpen] = useState<boolean>(false);
  const [isSettingsOpen, setIsSettingsOpen] = useState<boolean>(false);

  const loadForecast = useCallback(
    async (refresh: boolean = false) => {
      try {
        setIsLoading(true);
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

        const data = await fetchWeather(params);
        setWeatherData(data);
      } catch (err: any) {
        console.error('Failed to load weather:', err);
        setError(err.message || 'No se pudo conectar con el servidor meteorológico.');
      } finally {
        setIsLoading(false);
      }
    },
    [cityKey, gpsActive, customCoords]
  );

  // Initial load and whenever city/GPS changes
  useEffect(() => {
    loadForecast(false);
  }, [loadForecast]);

  // City selection handler
  const handleSelectCity = (selectedKey: string) => {
    setGpsActive(false);
    setCustomCoords(null);
    setCityKey(selectedKey);
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
        setCustomCoords({
          lat: position.coords.latitude,
          lon: position.coords.longitude,
        });
        setGpsActive(true);
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
        isLoading={isLoading}
        onSelectCity={handleSelectCity}
        onUseGps={handleUseGps}
        onRefresh={handleRefresh}
        onOpenAccuracy={() => setIsAccuracyOpen(true)}
        onOpenSettings={() => setIsSettingsOpen(true)}
        gpsActive={gpsActive}
      />

      {/* Main Content Area */}
      <main className="flex-1 w-full flex flex-col items-center">
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

            {/* 2. Hero Card: Current condition, key metrics, AI bias tag */}
            <HeroCurrent
              current={weatherData.current}
              firstHour={weatherData.hourly[0]}
              city={weatherData.city}
            />

            {/* 3. Hourly Forecast Strip (48h, day/night distinction, WRF 3x3 convective indicator) */}
            <HourlyStrip hourly={weatherData.hourly} />

            {/* 4. Live RainViewer Radar Map Component */}
            <RadarMap
              lat={weatherData.city.lat}
              lon={weatherData.city.lon}
              cityName={weatherData.city.name}
            />

            {/* 5. 7-Day Daily Forecast List */}
            <DailyForecastList daily={weatherData.daily} />

            {/* 6. Multi-Model & ML Bias Transparency Accordion */}
            <ModelsTransparency
              hourly={weatherData.hourly}
              mlMetadata={weatherData.ml_metadata}
              wrfAvailable={weatherData.wrf_available}
            />
          </>
        )}

        {/* Loading Skeleton during initial load */}
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
          Ensamble numérico (WRF-SMN 4km, ECMWF, ICON, GFS) + Corrección LightGBM SAOC + Nowcasting Radar RainViewer.
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

