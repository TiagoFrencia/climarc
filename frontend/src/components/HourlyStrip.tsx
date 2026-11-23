import React, { useState } from 'react';
import { Wind, CloudRain, AlertCircle, LayoutGrid, LineChart } from 'lucide-react';
import type { HourlyForecast } from '../types/weather';
import { WeatherIcon } from './WeatherIcon';
import {
  formatLocalHour,
  isDaylightInArgentina,
} from '../utils/weatherFormatters';
import { HourlyChartSVG } from './HourlyChartSVG';

interface HourlyStripProps {
  hourly: HourlyForecast[];
}

export const HourlyStrip: React.FC<HourlyStripProps> = ({ hourly }) => {
  const [viewMode, setViewMode] = useState<'cards' | 'chart'>('cards');

  // Take up to 48 hours for cards
  const displayHours = hourly.slice(0, 48);

  return (
    <section className="py-2 max-w-md mx-auto w-full">
      <div className="px-4 flex items-center justify-between mb-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
          Próximas 48 horas
        </h3>

        {/* View Switcher: Tarjetas | Gráfico 24h */}
        <div className="flex items-center bg-slate-900 p-0.5 rounded-xl border border-slate-800 text-[11px]">
          <button
            type="button"
            onClick={() => setViewMode('cards')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg font-medium transition-all ${
              viewMode === 'cards'
                ? 'bg-sky-500 text-white font-bold shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <LayoutGrid className="w-3 h-3" />
            <span>Tarjetas</span>
          </button>
          <button
            type="button"
            onClick={() => setViewMode('chart')}
            className={`flex items-center gap-1 px-2.5 py-1 rounded-lg font-medium transition-all ${
              viewMode === 'chart'
                ? 'bg-sky-500 text-white font-bold shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <LineChart className="w-3 h-3" />
            <span>Gráfico 24h</span>
          </button>
        </div>
      </div>

      {viewMode === 'chart' ? (
        <div className="px-4 py-1">
          <HourlyChartSVG hourly={hourly} />
        </div>
      ) : (
        <div className="flex gap-2.5 overflow-x-auto no-scrollbar px-4 py-1 -mx-0">
          {displayHours.map((step, idx) => {
            const isDay = isDaylightInArgentina(step.timestamp_utc);
            const hourStr = idx === 0 ? 'Ahora' : formatLocalHour(step.timestamp_utc);
            const hasRain = step.precip_prob_pct >= 20 || step.precip_mm > 0;

            // Check WRF 3x3 convective neighborhood warning:
            const nearbyRain = step.wrf_nearby_precip_max_mm;
            const hasNearbyCore =
              nearbyRain !== null &&
              nearbyRain !== undefined &&
              (nearbyRain - step.precip_mm >= 2.0 || nearbyRain >= 3.0);

            return (
              <div
                key={step.timestamp_utc || idx}
                className={`flex-shrink-0 w-[84px] rounded-2xl p-2.5 flex flex-col items-center justify-between text-center border transition-all ${
                  isDay
                    ? 'bg-gradient-to-b from-[#1E293B]/90 to-[#182335]/95 border-slate-700/60'
                    : 'bg-gradient-to-b from-[#111827]/90 to-[#0F172A]/95 border-indigo-950/70'
                }`}
              >
                {/* Hour & day/night badge */}
                <div className="w-full">
                  <span className="text-xs font-bold text-slate-200 block truncate">
                    {hourStr}
                  </span>
                  <span
                    className={`text-[9px] font-semibold px-1 rounded-sm uppercase tracking-tighter ${
                      isDay ? 'text-amber-400/90' : 'text-indigo-300/80'
                    }`}
                  >
                    {isDay ? 'Día' : 'Noche'}
                  </span>
                </div>

                {/* Weather Icon */}
                <div className="my-1.5 flex items-center justify-center">
                  <WeatherIcon
                    code={step.weather_code}
                    isDay={isDay}
                    className="w-7 h-7"
                  />
                </div>

                {/* Temperature */}
                <div className="text-sm font-extrabold text-slate-100">
                  {Math.round(step.temperature_c)}°
                </div>

                {/* Rain prob / mm */}
                <div className="w-full mt-1">
                  {hasRain ? (
                    <div className="flex flex-col items-center text-[10px] text-sky-400 font-semibold leading-tight">
                      <div className="flex items-center gap-0.5">
                        <CloudRain className="w-2.5 h-2.5" />
                        <span>{Math.round(step.precip_prob_pct)}%</span>
                      </div>
                      {step.precip_mm > 0 && (
                        <span className="text-[9px] text-sky-300">
                          {step.precip_mm.toFixed(1)} mm
                        </span>
                      )}
                    </div>
                  ) : (
                    <div className="text-[10px] text-slate-500">0%</div>
                  )}
                </div>

                {/* Wind Gusts */}
                <div className="mt-1 flex items-center gap-0.5 text-[10px] text-slate-400 font-medium">
                  <Wind className="w-2.5 h-2.5 text-slate-500" />
                  <span>{Math.round(step.wind_gust_kmh)} km/h</span>
                </div>

                {/* WRF 3x3 Neighborhood Convective Warning */}
                {hasNearbyCore && (
                  <div
                    title={`Alerta de celda cercana: WRF 3x3 detecta hasta ${nearbyRain.toFixed(1)} mm/h en inmediaciones de Río Cuarto.`}
                    className="mt-1.5 w-full py-0.5 px-1 rounded-md bg-amber-950/80 border border-amber-800/80 text-[9px] text-amber-300 font-bold flex items-center justify-center gap-0.5 shadow-sm"
                  >
                    <AlertCircle className="w-2.5 h-2.5 text-amber-400 shrink-0" />
                    <span className="truncate">Cercana</span>
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
};
