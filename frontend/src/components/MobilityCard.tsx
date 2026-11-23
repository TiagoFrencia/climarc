import React, { useState } from 'react';
import {
  Bike,
  Navigation,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Wind,
  CloudRain,
  Thermometer,
  Eye,
  Loader2,
  RefreshCw,
} from 'lucide-react';
import type {
  CurrentWeather,
  HourlyForecast,
  CityConfig,
  SentinelStationSummary,
  RouteWeatherResponse,
} from '../types/weather';
import { fetchRouteWeather } from '../services/api';

interface MobilityCardProps {
  current: CurrentWeather;
  hourly: HourlyForecast[];
  city: CityConfig;
  sentinels?: SentinelStationSummary[];
}

type TabMode = 'street' | 'route';

interface PresetsInfo {
  key: string;
  label: string;
  route: string;
}

const ROUTE_PRESETS: PresetsInfo[] = [
  { key: 'cordoba', label: 'Córdoba Capital', route: 'RN 36' },
  { key: 'villa_maria', label: 'Villa María', route: 'RN 158' },
  { key: 'villa_mercedes', label: 'Villa Mercedes', route: 'RN 8' },
  { key: 'santa_rosa_calamuchita', label: 'Santa Rosa Calamuchita', route: 'RP 23 / 5' },
  { key: 'san_luis', label: 'San Luis', route: 'RN 8 / 7' },
];

export const MobilityCard: React.FC<MobilityCardProps> = ({
  current,
  hourly,
  city,
  sentinels: _sentinels,
}) => {
  const [activeTab, setActiveTab] = useState<TabMode>('street');
  const [selectedDest, setSelectedDest] = useState<string>('cordoba');
  const [routeData, setRouteData] = useState<RouteWeatherResponse | null>(null);
  const [loadingRoute, setLoadingRoute] = useState<boolean>(false);
  const [routeError, setRouteError] = useState<string | null>(null);

  // --- CALLE / MOTO EVALUATION (4 Real Factors) ---
  const firstH = hourly[0];
  const secondH = hourly[1];

  // 1. Piso / Lluvia Inminente
  const upcomingRain = Math.max(firstH?.precip_mm || 0, secondH?.precip_mm || 0);
  const currentRain = current.is_precipitating_now ? Math.max(0.2, upcomingRain) : upcomingRain;
  const nearbyRain = firstH?.wrf_nearby_precip_max_mm || 0;
  const isHeavyRain = currentRain >= 5.0 || nearbyRain >= 8.0 || [95, 96, 99].includes(current.weather_code);
  const isLightRain = currentRain >= 0.2 || nearbyRain >= 2.5 || (firstH?.precip_prob_pct || 0) >= 40;

  let roadFactor: { status: 'optimo' | 'precaucion' | 'peligroso'; text: string } = {
    status: 'optimo',
    text: 'Calzada seca. Sin lluvia en curso ni en aproximación inmediata.',
  };
  if (isHeavyRain) {
    const isNearbyWrf = nearbyRain >= 8.0 && currentRain < 5.0 && ![95, 96, 99].includes(current.weather_code);
    roadFactor = {
      status: 'peligroso',
      text: isNearbyWrf
        ? 'Tormenta severa en aproximación perimetral (WRF 3x3). Piso muy resbaladizo.'
        : 'Piso muy resbaladizo o con charcos profundos. Tormenta / lluvia intensa.',
    };
  } else if (isLightRain) {
    const isNearbyWrf = nearbyRain >= 2.5 && currentRain < 0.2;
    roadFactor = {
      status: 'precaucion',
      text: isNearbyWrf
        ? 'Lluvia débil en aproximación perimetral (WRF 3x3). Frenado comprometido.'
        : 'Asfalto húmedo o probabilidad de precipitaciones. Frenado comprometido.',
    };
  }

  // 2. Viento y Ráfagas Laterales (Motos / Bicis)
  const gust = Math.max(current.wind_gust_kmh, firstH?.wind_gust_kmh || 0);
  let windFactor: { status: 'optimo' | 'precaucion' | 'peligroso'; text: string } = {
    status: 'optimo',
    text: `Viento suave/moderado (${Math.round(current.wind_speed_kmh)} km/h). Estable.`,
  };
  if (gust >= 55.0) {
    windFactor = {
      status: 'peligroso',
      text: `Ráfagas violentas (${Math.round(gust)} km/h). Desestabilización severa en dos ruedas.`,
    };
  } else if (gust >= 40.0) {
    windFactor = {
      status: 'precaucion',
      text: `Ráfagas laterales de ${Math.round(gust)} km/h. Conducir a baja velocidad y sujetar firme.`,
    };
  }

  // 3. Sensación Térmica
  const appTemp = current.apparent_temperature_c;
  let tempFactor: { status: 'optimo' | 'precaucion' | 'peligroso'; text: string } = {
    status: 'optimo',
    text: `Sensación térmica agradable (${Math.round(appTemp)} °C).`,
  };
  if (appTemp >= 38.0 || appTemp <= 2.0) {
    tempFactor = {
      status: 'peligroso',
      text: appTemp >= 38.0 ? `Calor extremo (ST ${Math.round(appTemp)} °C). Riesgo de descompensación.` : `Frío glacial (ST ${Math.round(appTemp)} °C). Riesgo de escarcha/hielo.`,
    };
  } else if (appTemp >= 34.0 || appTemp <= 7.0) {
    tempFactor = {
      status: 'precaucion',
      text: appTemp >= 34.0 ? `Calor intenso (ST ${Math.round(appTemp)} °C). Hidratación clave.` : `Frío intenso (ST ${Math.round(appTemp)} °C). Ropa térmica recomendada.`,
    };
  }

  // 4. Visibilidad / Niebla SAOC
  const rawMetar = (current.metar_raw || '').toUpperCase();
  const wxCodes = current.metar_wx_raw ? current.metar_wx_raw.toUpperCase() : '';
  const isDenseFog = rawMetar.includes(' FG ') || wxCodes.includes('FG');
  const isLightFog = rawMetar.includes(' BR ') || rawMetar.includes('HZ') || wxCodes.includes('BR') || (current.relative_humidity_pct || 0) >= 95;

  let visFactor: { status: 'optimo' | 'precaucion' | 'peligroso'; text: string } = {
    status: 'optimo',
    text: 'Visibilidad normal (mayor a 8 km).',
  };
  if (isDenseFog) {
    visFactor = {
      status: 'peligroso',
      text: 'Niebla densa en SAOC (< 1.000 m). Peligro en cruces y avenidas rápidas.',
    };
  } else if (isLightFog) {
    visFactor = {
      status: 'precaucion',
      text: 'Bruma o neblina baja en aeródromo. Luces bajas encendidas.',
    };
  }

  // Global City/Street Semaphore
  const factors = [roadFactor.status, windFactor.status, tempFactor.status, visFactor.status];
  let streetSemaphore: { level: 'optimo' | 'precaucion' | 'peligroso'; title: string; badgeClass: string; icon: any } = {
    level: 'optimo',
    title: 'Condiciones Óptimas',
    badgeClass: 'bg-emerald-950/80 border-emerald-600/70 text-emerald-300',
    icon: CheckCircle2,
  };
  if (factors.includes('peligroso')) {
    streetSemaphore = {
      level: 'peligroso',
      title: 'Evitar Salir en Moto / Bici',
      badgeClass: 'bg-rose-950/90 border-rose-600/80 text-rose-200 animate-pulse',
      icon: XCircle,
    };
  } else if (factors.includes('precaucion')) {
    streetSemaphore = {
      level: 'precaucion',
      title: 'Circulación con Precaución',
      badgeClass: 'bg-amber-950/80 border-amber-600/70 text-amber-300',
      icon: AlertTriangle,
    };
  }

  // --- ROUTE CHECKER HANDLER ---
  const handleCheckRoute = async (destKey: string) => {
    setSelectedDest(destKey);
    setLoadingRoute(true);
    setRouteError(null);
    try {
      const data = await fetchRouteWeather({ origin: 'rio_cuarto', destination: destKey });
      setRouteData(data);
    } catch (err: any) {
      setRouteError(err.message || 'No se pudo consultar el pronóstico de ruta.');
    } finally {
      setLoadingRoute(false);
    }
  };

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl overflow-hidden p-4">
        {/* Header with Segmented Tabs */}
        <div className="flex items-center justify-between mb-3 border-b border-slate-800/80 pb-3">
          <div className="flex items-center gap-2">
            <div className="p-1.5 rounded-xl bg-slate-800 text-sky-400">
              {activeTab === 'street' ? <Bike className="w-4 h-4" /> : <Navigation className="w-4 h-4" />}
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-100">
                {activeTab === 'street' ? 'Semáforo Calle / Moto' : 'Chequear Estado de Ruta'}
              </h3>
              <p className="text-[10.5px] text-slate-400">
                {activeTab === 'street'
                  ? `Decisión rápida para ${city.name} y dos ruedas`
                  : `Corredores interurbanos desde ${city.name}`}
              </p>
            </div>
          </div>

          {/* Tab Selector */}
          <div className="flex bg-slate-900 p-0.5 rounded-xl border border-slate-800 text-[11px]">
            <button
              type="button"
              onClick={() => setActiveTab('street')}
              className={`px-2.5 py-1 rounded-lg font-semibold transition-all ${
                activeTab === 'street'
                  ? 'bg-sky-500 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Calle
            </button>
            <button
              type="button"
              onClick={() => {
                setActiveTab('route');
                if (!routeData && !loadingRoute) {
                  handleCheckRoute(selectedDest);
                }
              }}
              className={`px-2.5 py-1 rounded-lg font-semibold transition-all ${
                activeTab === 'route'
                  ? 'bg-sky-500 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Ruta
            </button>
          </div>
        </div>

        {/* TAB 1: CALLE / MOTO */}
        {activeTab === 'street' && (
          <div className="space-y-3">
            {/* Global Semaphore Badge */}
            <div className={`p-3 rounded-2xl border flex items-center justify-between ${streetSemaphore.badgeClass}`}>
              <div className="flex items-center gap-2.5">
                <streetSemaphore.icon className="w-5 h-5 shrink-0" />
                <div>
                  <span className="text-xs uppercase font-extrabold tracking-wider block">
                    {streetSemaphore.title}
                  </span>
                  <span className="text-[11px] opacity-90 font-medium">
                    {streetSemaphore.level === 'optimo'
                      ? 'Asfalto seguro, sin ráfagas peligrosas'
                      : streetSemaphore.level === 'precaucion'
                      ? 'Atención a frenado o ráfagas de viento'
                      : 'Condiciones adversas detectadas'}
                  </span>
                </div>
              </div>
            </div>

            {/* 4 Factor Breakdown */}
            <div className="grid grid-cols-2 gap-2 text-[11px]">
              {/* Factor 1: Piso / Lluvia */}
              <div className="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800 flex flex-col justify-between">
                <div className="flex items-center justify-between text-slate-400 mb-1">
                  <span className="flex items-center gap-1 font-semibold text-slate-300">
                    <CloudRain className="w-3.5 h-3.5 text-sky-400" />
                    Piso / Lluvia
                  </span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      roadFactor.status === 'optimo'
                        ? 'bg-emerald-400'
                        : roadFactor.status === 'precaucion'
                        ? 'bg-amber-400'
                        : 'bg-rose-400'
                    }`}
                  />
                </div>
                <p className="text-[10px] text-slate-400 leading-tight">
                  {roadFactor.text}
                </p>
              </div>

              {/* Factor 2: Ráfagas Cruzadas */}
              <div className="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800 flex flex-col justify-between">
                <div className="flex items-center justify-between text-slate-400 mb-1">
                  <span className="flex items-center gap-1 font-semibold text-slate-300">
                    <Wind className="w-3.5 h-3.5 text-sky-400" />
                    Viento Lateral
                  </span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      windFactor.status === 'optimo'
                        ? 'bg-emerald-400'
                        : windFactor.status === 'precaucion'
                        ? 'bg-amber-400'
                        : 'bg-rose-400'
                    }`}
                  />
                </div>
                <p className="text-[10px] text-slate-400 leading-tight">
                  {windFactor.text}
                </p>
              </div>

              {/* Factor 3: Sensación Térmica */}
              <div className="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800 flex flex-col justify-between">
                <div className="flex items-center justify-between text-slate-400 mb-1">
                  <span className="flex items-center gap-1 font-semibold text-slate-300">
                    <Thermometer className="w-3.5 h-3.5 text-amber-400" />
                    Térmica
                  </span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      tempFactor.status === 'optimo'
                        ? 'bg-emerald-400'
                        : tempFactor.status === 'precaucion'
                        ? 'bg-amber-400'
                        : 'bg-rose-400'
                    }`}
                  />
                </div>
                <p className="text-[10px] text-slate-400 leading-tight">
                  {tempFactor.text}
                </p>
              </div>

              {/* Factor 4: Visibilidad */}
              <div className="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800 flex flex-col justify-between">
                <div className="flex items-center justify-between text-slate-400 mb-1">
                  <span className="flex items-center gap-1 font-semibold text-slate-300">
                    <Eye className="w-3.5 h-3.5 text-indigo-400" />
                    Visibilidad SAOC
                  </span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      visFactor.status === 'optimo'
                        ? 'bg-emerald-400'
                        : visFactor.status === 'precaucion'
                        ? 'bg-amber-400'
                        : 'bg-rose-400'
                    }`}
                  />
                </div>
                <p className="text-[10px] text-slate-400 leading-tight">
                  {visFactor.text}
                </p>
              </div>
            </div>
          </div>
        )}

        {/* TAB 2: CHEQUEAR RUTA */}
        {activeTab === 'route' && (
          <div className="space-y-3">
            {/* Destination Preset Selector Pills */}
            <div className="flex gap-1.5 overflow-x-auto no-scrollbar py-1">
              {ROUTE_PRESETS.map((p) => {
                const isSelected = selectedDest === p.key;
                return (
                  <button
                    key={p.key}
                    type="button"
                    onClick={() => handleCheckRoute(p.key)}
                    className={`px-2.5 py-1.5 rounded-xl text-[11px] font-bold shrink-0 border transition-all ${
                      isSelected
                        ? 'bg-sky-500/20 border-sky-400 text-sky-200 shadow-sm'
                        : 'bg-slate-900/80 border-slate-800 text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    <span>{p.label}</span>
                    <span className="block text-[9px] font-normal text-slate-400">{p.route}</span>
                  </button>
                );
              })}
            </div>

            {/* Route Loading State */}
            {loadingRoute && (
              <div className="py-8 flex flex-col items-center justify-center space-y-2 text-slate-400 text-xs">
                <Loader2 className="w-6 h-6 animate-spin text-sky-400" />
                <span>Analizando telemetría de 3 puntos en corredor...</span>
              </div>
            )}

            {/* Route Error State */}
            {routeError && !loadingRoute && (
              <div className="p-3 rounded-2xl bg-rose-950/60 border border-rose-800 text-rose-200 text-xs flex items-center justify-between">
                <span>{routeError}</span>
                <button
                  onClick={() => handleCheckRoute(selectedDest)}
                  className="px-2 py-1 bg-rose-900/60 rounded-lg text-[11px] font-semibold flex items-center gap-1"
                >
                  <RefreshCw className="w-3 h-3" /> Reintentar
                </button>
              </div>
            )}

            {/* Route Results Display */}
            {routeData && !loadingRoute && (
              <div className="space-y-3 animate-in fade-in duration-200">
                {/* Global Corridor Semaphore Banner */}
                <div
                  className={`p-3 rounded-2xl border flex items-center justify-between ${
                    routeData.global_status === 'optimo'
                      ? 'bg-emerald-950/80 border-emerald-600/70 text-emerald-200'
                      : routeData.global_status === 'precaucion'
                      ? 'bg-amber-950/80 border-amber-600/70 text-amber-200'
                      : 'bg-rose-950/90 border-rose-600/80 text-rose-200 animate-pulse'
                  }`}
                >
                  <div>
                    <div className="text-[10px] uppercase font-bold tracking-wider opacity-80">
                      {routeData.route_name}
                    </div>
                    <div className="text-xs font-extrabold capitalize mt-0.5">
                      Semáforo Global: {routeData.global_status}
                    </div>
                    <div className="text-[11px] mt-1 leading-snug opacity-95">
                      {routeData.summary}
                    </div>
                  </div>
                </div>

                {/* 3 Segments Cards (Origin -> Midpoint -> Destination) */}
                <div className="space-y-1.5">
                  <div className="flex items-center justify-between text-[10px] uppercase font-bold text-slate-400 px-1">
                    <span>Tramos del trayecto</span>
                    <span>Lluvia · Ráfagas</span>
                  </div>

                  {routeData.segments.map((seg) => {
                    const statusDotClass =
                      seg.status === 'optimo'
                        ? 'bg-emerald-400'
                        : seg.status === 'precaucion'
                        ? 'bg-amber-400'
                        : 'bg-rose-400';

                    return (
                      <div
                        key={seg.segment_id}
                        className="p-2.5 rounded-xl bg-slate-900/80 border border-slate-800 text-xs flex items-center justify-between gap-2"
                      >
                        <div className="min-w-0 flex items-center gap-2">
                          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${statusDotClass}`} />
                          <div className="min-w-0">
                            <span className="font-bold text-slate-200 block truncate">
                              {seg.name}
                            </span>
                            <span className="text-[10.5px] text-slate-400 truncate block">
                              {seg.condition} · {seg.temperature_c}°C
                            </span>
                          </div>
                        </div>

                        <div className="text-right shrink-0">
                          <div className="text-slate-200 font-bold text-[11px]">
                            {seg.rain_mm > 0 ? (
                              <span className="text-sky-300">{seg.rain_mm.toFixed(1)} mm</span>
                            ) : (
                              <span className="text-slate-400">0 mm</span>
                            )}
                            <span className="text-slate-600 mx-1">·</span>
                            <span className="text-amber-300">{Math.round(seg.wind_gust_kmh)} km/h</span>
                          </div>
                          <div className="text-[10px] text-slate-400">
                            Vis: {seg.visibility_km ? `${seg.visibility_km} km` : 'Normal'}
                          </div>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </section>
  );
};
