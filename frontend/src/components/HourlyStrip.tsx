import React from 'react';
import { Wind, CloudRain, AlertCircle } from 'lucide-react';
import type { HourlyForecast } from '../types/weather';
import { WeatherIcon } from './WeatherIcon';
import {
  formatLocalHour,
  isDaylightInArgentina,
} from '../utils/weatherFormatters';

interface HourlyStripProps {
  hourly: HourlyForecast[];
}

export const HourlyStrip: React.FC<HourlyStripProps> = ({ hourly }) => {
  // Take up to 48 hours for the strip
  const displayHours = hourly.slice(0, 48);

  return (
    <section className="py-2 max-w-md mx-auto w-full">
      <div className="px-4 flex items-center justify-between mb-2">
        <h3 className="text-xs font-bold uppercase tracking-wider text-slate-400">
          Próximas 48 horas
        </h3>
        <span className="text-[11px] text-slate-500 font-medium">
          Hora de Argentina (UTC-3)
        </span>
      </div>

      <div className="flex gap-2.5 overflow-x-auto no-scrollbar px-4 py-1 -mx-0">
        {displayHours.map((step, idx) => {
          const isDay = isDaylightInArgentina(step.timestamp_utc);
          const hourStr = idx === 0 ? 'Ahora' : formatLocalHour(step.timestamp_utc);
          const hasRain = step.precip_prob_pct >= 20 || step.precip_mm > 0;

          // Check WRF 3x3 convective neighborhood warning:
          // If wrf_nearby_precip_max_mm exceeds central point by >= 2mm or is >= 3 mm/h
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
                <span>{Math.round(step.wind_gust_kmh)}</span>
                <span className="text-[8px] text-slate-500">km/h</span>
              </div>

              {/* WRF 3x3 Neighborhood convective core badge with uniform height */}
              <div className="w-full min-h-[22px] mt-1.5 flex items-center justify-center">
                {hasNearbyCore ? (
                  <div
                    title={`Modelo WRF-SMN 3x3: Se detecta núcleo convectivo de ${nearbyRain.toFixed(1)} mm a menos de 12 km.`}
                    className="w-full py-0.5 px-1 rounded bg-amber-500/20 border border-amber-500/40 text-amber-300 text-[9px] font-extrabold flex items-center justify-center gap-0.5 whitespace-nowrap"
                  >
                    <AlertCircle className="w-2.5 h-2.5 shrink-0" />
                    <span>⚡ ~{nearbyRain.toFixed(1)}mm</span>
                  </div>
                ) : null}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
};
