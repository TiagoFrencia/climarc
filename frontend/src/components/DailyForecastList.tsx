import React from 'react';
import { Calendar, CloudRain, Wind } from 'lucide-react';
import type { DailyForecast } from '../types/weather';
import { WeatherIcon } from './WeatherIcon';
import { formatSpanishDay } from '../utils/weatherFormatters';

interface DailyForecastListProps {
  daily: DailyForecast[];
}

export const DailyForecastList: React.FC<DailyForecastListProps> = ({ daily }) => {
  if (!daily || daily.length === 0) return null;

  // Find overall min and max across all days to render proportional temp bar
  const allMins = daily.map((d) => d.temperature_min_c);
  const allMaxs = daily.map((d) => d.temperature_max_c);
  const overallMin = Math.min(...allMins);
  const overallMax = Math.max(...allMaxs);
  const totalSpan = Math.max(1, overallMax - overallMin);

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-sky-400" />
            <h3 className="text-sm font-bold text-slate-100">
              Pronóstico para 7 días
            </h3>
          </div>
          <span className="text-[10px] text-slate-400 font-medium">
            Ensamble ECMWF/ICON/GFS
          </span>
        </div>

        <div className="divide-y divide-slate-800/80">
          {daily.map((day, idx) => {
            const dayLabel = formatSpanishDay(day.date, idx);
            const minT = Math.round(day.temperature_min_c);
            const maxT = Math.round(day.temperature_max_c);
            const hasRain = day.precipitation_probability_max_pct >= 25 || day.precipitation_sum_mm > 0.5;

            // Bar geometry
            const leftPercent = Math.max(0, ((day.temperature_min_c - overallMin) / totalSpan) * 100);
            const widthPercent = Math.max(8, ((day.temperature_max_c - day.temperature_min_c) / totalSpan) * 100);

            return (
              <div
                key={day.date}
                className="py-2.5 flex items-center justify-between gap-2 text-xs"
              >
                {/* Day name */}
                <div className="w-16 font-semibold text-slate-200 truncate">
                  {dayLabel}
                </div>

                {/* Weather Icon */}
                <div className="w-7 flex items-center justify-center">
                  <WeatherIcon code={day.weather_code} isDay={true} className="w-5 h-5" />
                </div>

                {/* Rain indicator */}
                <div className="w-14 text-center">
                  {hasRain ? (
                    <div className="flex flex-col items-center">
                      <span className="text-[11px] font-bold text-sky-400 flex items-center gap-0.5">
                        <CloudRain className="w-2.5 h-2.5" />
                        {Math.round(day.precipitation_probability_max_pct)}%
                      </span>
                      {day.precipitation_sum_mm > 0 && (
                        <span className="text-[9px] text-slate-400 font-medium">
                          {day.precipitation_sum_mm.toFixed(1)}mm
                        </span>
                      )}
                    </div>
                  ) : (
                    <span className="text-[10px] text-slate-600 font-medium">0%</span>
                  )}
                </div>

                {/* Min/Max Temperature Visual Bar */}
                <div className="flex-1 flex items-center gap-2">
                  <span className="w-6 text-right text-slate-400 font-medium">{minT}°</span>
                  <div className="flex-1 h-1.5 bg-slate-800 rounded-full relative overflow-hidden">
                    <div
                      className="absolute top-0 bottom-0 rounded-full bg-gradient-to-r from-sky-400 via-amber-300 to-rose-400"
                      style={{
                        left: `${leftPercent}%`,
                        width: `${widthPercent}%`,
                      }}
                    />
                  </div>
                  <span className="w-6 text-left text-slate-100 font-bold">{maxT}°</span>
                </div>

                {/* Max Gust */}
                <div className="w-12 text-right text-[10px] text-slate-400 font-medium flex items-center justify-end gap-0.5">
                  <Wind className="w-2.5 h-2.5 text-slate-500" />
                  <span>{Math.round(day.wind_speed_max_kmh)}</span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
};
