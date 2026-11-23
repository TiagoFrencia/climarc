import React from 'react';
import {
  Wind,
  Droplets,
  Gauge,
  CloudRain,
  Cpu,
  ArrowUp,
} from 'lucide-react';
import type { CurrentWeather, HourlyForecast, CityConfig } from '../types/weather';
import { WeatherIcon } from './WeatherIcon';
import {
  getWeatherCondition,
  degreesToCardinal,
  isDaylightInArgentina,
} from '../utils/weatherFormatters';

interface HeroCurrentProps {
  current: CurrentWeather;
  firstHour?: HourlyForecast;
  nextHour?: HourlyForecast;
  city: CityConfig;
}

function getComfortLabel(dewPointC: number): string {
  if (dewPointC < 10) return 'Seco';
  if (dewPointC <= 16) return 'Agradable';
  if (dewPointC <= 20) return 'Húmedo';
  return 'Pesado';
}

export const HeroCurrent: React.FC<HeroCurrentProps> = ({
  current,
  firstHour,
  nextHour,
  city,
}) => {
  const isDay = isDaylightInArgentina(current.timestamp_utc);
  const condition = getWeatherCondition(current.weather_code, isDay);
  const windDirCardinal = degreesToCardinal(current.wind_direction_deg);

  // Precipitation info: check hourly[0] and nextHour
  const precipMm = firstHour ? firstHour.precip_mm : 0.0;
  const nextHourMm = nextHour ? nextHour.precip_mm : 0.0;
  const maxPrecipMm = Math.max(precipMm, nextHourMm);
  const precipProb = firstHour ? firstHour.precip_prob_pct : 0;
  const isPrecipNow = Boolean(current.is_precipitating_now);

  // Temperature formatted
  const tempRounded = Math.round(current.temperature_c);
  const appTempRounded = Math.round(current.apparent_temperature_c);

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="relative overflow-hidden rounded-3xl bg-gradient-to-b from-[#1E293B]/90 to-[#111827]/95 border border-slate-800/90 shadow-2xl p-5">
        {/* Subtle decorative glow */}
        <div className="absolute -top-16 -right-16 w-44 h-44 rounded-full bg-sky-500/10 blur-3xl pointer-events-none" />

        {/* Location header inside card */}
        <div className="flex items-center justify-between mb-2">
          <div>
            <h2 className="text-xl font-extrabold text-slate-100 tracking-tight">
              {city.name}
            </h2>
            <p className="text-xs text-slate-400">
              Estación {city.icao} · Ensamble multi-modelo
              {city.name.includes('(GPS)') && (
                <span className="block text-[10px] text-slate-400/90 font-mono mt-0.5">
                  Coord: {city.lat.toFixed(3)}, {city.lon.toFixed(3)}
                </span>
              )}
            </p>
          </div>

          {/* AI Correction Badge */}
          {current.ml_applied && (
            <div
              title={`Ajuste LightGBM entrenado con 15.573 hs SAOC: Temp ${current.ml_temp_adjustment_c > 0 ? '+' : ''}${current.ml_temp_adjustment_c} °C, Viento ${current.ml_wind_adjustment_kmh > 0 ? '+' : ''}${current.ml_wind_adjustment_kmh} km/h`}
              className="flex items-center gap-1 px-2.5 py-1 rounded-full bg-cyan-950/80 border border-cyan-800/70 text-cyan-300 text-[11px] font-semibold shadow-sm"
            >
              <Cpu className="w-3 h-3 text-cyan-400" />
              <span>
                IA {current.ml_temp_adjustment_c > 0 ? '+' : ''}
                {current.ml_temp_adjustment_c.toFixed(1)} °C
              </span>
            </div>
          )}
        </div>

        {/* Main temperature and weather display */}
        <div className="flex items-center justify-between py-2">
          <div>
            <div className="flex items-baseline">
              <span className="text-6xl font-black text-slate-50 tracking-tighter">
                {tempRounded}
              </span>
              <span className="text-3xl font-light text-sky-400 ml-0.5">°C</span>
            </div>
            <div className="text-xs text-slate-400 mt-0.5 font-medium">
              Sensación térmica{' '}
              <span className="text-slate-200 font-semibold">{appTempRounded} °C</span>
            </div>
            <div className="text-sm font-semibold text-slate-200 mt-1">
              {condition.label}
            </div>
          </div>

          <div className="flex flex-col items-center justify-center p-3 rounded-2xl bg-slate-800/40 border border-slate-700/40">
            <WeatherIcon
              code={current.weather_code}
              isDay={isDay}
              className="w-16 h-16 drop-shadow-md"
            />
          </div>
        </div>

        {/* Grid of Key Metrics */}
        <div className="grid grid-cols-2 gap-2 mt-4 pt-4 border-t border-slate-800/80 text-xs">
          {/* Wind + Gusts */}
          <div className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60">
            <div className="p-2 rounded-lg bg-sky-950/60 text-sky-400">
              <Wind className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] text-slate-400 uppercase font-medium">
                Viento / Ráfagas
              </div>
              <div className="font-bold text-slate-200 flex items-center gap-1 truncate">
                <span>{Math.round(current.wind_speed_kmh)} km/h</span>
                <span className="text-[10px] text-slate-400">({windDirCardinal})</span>
                <ArrowUp
                  className="w-3 h-3 text-sky-400 transition-transform inline"
                  style={{ transform: `rotate(${current.wind_direction_deg}deg)` }}
                />
              </div>
              <div className="text-[10px] text-amber-300 font-medium">
                Ráf: {Math.round(current.wind_gust_kmh)} km/h
              </div>
            </div>
          </div>

          {/* Humidity & Real Dew Point */}
          <div className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60">
            <div className="p-2 rounded-lg bg-sky-950/60 text-sky-400">
              <Droplets className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] text-slate-400 uppercase font-medium">
                Humedad
              </div>
              <div className="font-bold text-slate-200 text-sm">
                {current.relative_humidity_pct !== null
                  ? `${Math.round(current.relative_humidity_pct)}%`
                  : '--%'}
              </div>
              <div className="text-[10px] text-slate-300 leading-tight">
                {current.dew_point_c !== null && current.dew_point_c !== undefined
                  ? `Rocío ${Math.round(current.dew_point_c)}° · ${getComfortLabel(current.dew_point_c)}`
                  : 'Punto de rocío'}
              </div>
            </div>
          </div>

          {/* Sea Level Pressure QNH */}
          <div className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60">
            <div className="p-2 rounded-lg bg-indigo-950/60 text-indigo-400">
              <Gauge className="w-4 h-4" />
            </div>
            <div>
              <div className="text-[10px] text-slate-400 uppercase font-medium">
                Presión QNH
              </div>
              <div className="font-bold text-slate-200 text-sm">
                {current.pressure_hpa !== null
                  ? `${current.pressure_hpa.toFixed(1)} hPa`
                  : '-- hPa'}
              </div>
              <div className="text-[10px] text-slate-400">
                A nivel del mar
              </div>
            </div>
          </div>

          {/* Rain current / next hour */}
          <div className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60">
            <div className="p-2 rounded-lg bg-blue-950/60 text-blue-400">
              <CloudRain className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] text-slate-400 uppercase font-medium">
                Lluvia Próx. Hora
              </div>
              <div className="font-bold text-slate-200 text-sm">
                {isPrecipNow && maxPrecipMm === 0 ? (
                  <span className="text-sky-300 font-extrabold animate-pulse">Lloviendo ahora</span>
                ) : maxPrecipMm > 0 ? (
                  `${maxPrecipMm.toFixed(1)} mm`
                ) : (
                  '0 mm'
                )}
              </div>
              <div className="text-[10px] text-slate-400">
                {isPrecipNow ? (
                  maxPrecipMm > 0 ? (
                    <span className="text-sky-400 font-medium">Lloviendo ahora</span>
                  ) : current.metar_wx_raw ? (
                    `Reporte: ${current.metar_wx_raw}`
                  ) : (
                    'Detectado en pista'
                  )
                ) : (
                  `Prob: ${precipProb}%`
                )}
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};
