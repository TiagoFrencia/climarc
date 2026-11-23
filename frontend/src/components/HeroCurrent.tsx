import React, { useState } from 'react';
import {
  Wind,
  Droplets,
  Gauge,
  CloudRain,
  Cpu,
  ArrowUp,
  ShieldAlert,
  Sparkles,
  Sun,
  Sunrise,
  Sunset,
  ChevronDown,
  ChevronUp,
} from 'lucide-react';
import type { CurrentWeather, HourlyForecast, CityConfig, HailRiskEvaluation } from '../types/weather';
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
  summaryOneLiner?: string;
  hailRisk?: HailRiskEvaluation;
}

function getComfortLabel(dewPointC: number, rhPct?: number | null): string {
  if (rhPct !== undefined && rhPct !== null && rhPct >= 90) return 'Húmedo';
  const roundedDp = Math.round(dewPointC);
  if (roundedDp < 10) return 'Seco';
  if (roundedDp < 16) return 'Agradable';
  if (roundedDp <= 20) return 'Húmedo';
  return 'Pesado';
}

function getUvBadge(uv: number): { label: string; colorClass: string } {
  if (uv < 3) return { label: 'Bajo', colorClass: 'text-emerald-400 bg-emerald-950/60 border-emerald-800/60' };
  if (uv < 6) return { label: 'Moderado', colorClass: 'text-amber-400 bg-amber-950/60 border-amber-800/60' };
  if (uv < 8) return { label: 'Alto', colorClass: 'text-orange-400 bg-orange-950/60 border-orange-800/60' };
  if (uv < 11) return { label: 'Muy Alto', colorClass: 'text-rose-400 bg-rose-950/60 border-rose-800/60' };
  return { label: 'Extremo', colorClass: 'text-purple-400 bg-purple-950/60 border-purple-800/60' };
}

export const HeroCurrent: React.FC<HeroCurrentProps> = ({
  current,
  firstHour,
  nextHour,
  city,
  summaryOneLiner,
  hailRisk,
}) => {
  const [isHailExpanded, setIsHailExpanded] = useState<boolean>(false);

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

  // Dynamic subtle gradient background based on current condition and local time
  const code = current.weather_code;
  const isStorm = code === 95 || code === 96 || code === 99 || Boolean(hailRisk?.imminent_hail_alert);
  const isRain = code >= 50 && code < 90;
  const isFogOrOvercast = code === 3 || code === 45 || code === 48;

  let bgGradient = 'from-[#1E293B]/90 via-[#182335]/95 to-[#111827]/98 border-slate-800/90';
  let glowColor = 'bg-sky-500/10';

  if (isStorm) {
    bgGradient = 'from-purple-950/70 via-slate-900/90 to-[#0F172A]/98 border-purple-800/60';
    glowColor = 'bg-purple-600/20';
  } else if (isRain) {
    bgGradient = 'from-blue-950/70 via-slate-900/90 to-[#0F172A]/98 border-blue-800/60';
    glowColor = 'bg-blue-600/15';
  } else if (isFogOrOvercast) {
    bgGradient = 'from-slate-800/80 via-slate-900/90 to-[#111827]/98 border-slate-700/60';
    glowColor = 'bg-slate-500/10';
  } else if (isDay) {
    bgGradient = 'from-sky-950/60 via-slate-900/90 to-[#111827]/98 border-sky-800/50';
    glowColor = 'bg-amber-400/15';
  } else {
    bgGradient = 'from-indigo-950/60 via-slate-900/90 to-[#0B1120]/98 border-indigo-900/50';
    glowColor = 'bg-indigo-500/15';
  }

  // QNH tendency label & color (compact 1-line format to avoid truncation: ↘ -2.0 hPa/3h / ↗ +1.8 hPa/3h / → Estable)
  const qnhTrend = current.qnh_trend;
  const qnhDelta = current.qnh_delta_3h_hpa;
  const hasDelta = qnhDelta !== undefined && qnhDelta !== null && Math.abs(qnhDelta) >= 0.1;
  const deltaStr = hasDelta ? `${qnhDelta > 0 ? '+' : ''}${qnhDelta.toFixed(1)} hPa/3h` : '';

  let qnhTrendText = '→ Estable';
  let qnhTrendColor = 'text-slate-400';

  if (qnhTrend === 'subiendo') {
    qnhTrendText = hasDelta ? `↗ ${deltaStr}` : '↗ Subiendo';
    qnhTrendColor = 'text-emerald-400';
  } else if (qnhTrend === 'bajando') {
    qnhTrendText = hasDelta ? `↘ ${deltaStr}` : '↘ Bajando';
    qnhTrendColor = 'text-amber-400';
  } else if (qnhTrend === 'bajando_rapido') {
    qnhTrendText = hasDelta ? `⏬ ${deltaStr}` : '⏬ Bajando rápido';
    qnhTrendColor = 'text-rose-400 font-bold';
  } else {
    qnhTrendText = hasDelta ? `→ ${deltaStr}` : '→ Estable';
  }

  // Hail Chip logic: only render if level !== 'sin_riesgo'
  const hasHailRisk = hailRisk && hailRisk.level !== 'sin_riesgo';
  let hailBadgeClass = 'bg-amber-950/80 border-amber-600/80 text-amber-200';
  if (hailRisk?.level === 'alto') {
    hailBadgeClass = 'bg-orange-950/80 border-orange-600/80 text-orange-200';
  } else if (hailRisk?.level === 'extremo') {
    hailBadgeClass = 'bg-rose-950/90 border-rose-600/90 text-rose-200 animate-pulse';
  }

  // UV display condition
  const uvVal = current.uv_index;
  const showUv = isDay || (uvVal !== null && uvVal !== undefined && uvVal > 0);

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className={`relative overflow-hidden rounded-3xl bg-gradient-to-b ${bgGradient} border shadow-2xl p-5 transition-colors duration-500`}>
        {/* Subtle decorative glow */}
        <div className={`absolute -top-16 -right-16 w-44 h-44 rounded-full ${glowColor} blur-3xl pointer-events-none transition-colors duration-500`} />

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

          <div className="flex items-center gap-1.5">
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
        </div>

        {/* Resumen Ejecutivo (1-liner sin truncar, hasta 2 líneas) */}
        {summaryOneLiner && (
          <div className="mb-2 py-1.5 px-3 rounded-xl bg-slate-900/70 border border-slate-800/80 text-xs font-medium text-slate-200 leading-snug flex items-start gap-2 shadow-inner">
            <Sparkles className="w-3.5 h-3.5 text-sky-400 shrink-0 mt-0.5" />
            <span className="leading-snug line-clamp-2">{summaryOneLiner}</span>
          </div>
        )}

        {/* Contextual Anti-Hail Chip (Only if hail_risk !== 'sin_riesgo') */}
        {hasHailRisk && (
          <div className="mb-3">
            <button
              type="button"
              onClick={() => setIsHailExpanded(!isHailExpanded)}
              className={`w-full flex items-center justify-between px-3 py-1.5 rounded-xl border text-xs font-bold transition-all shadow-sm ${hailBadgeClass}`}
            >
              <div className="flex items-center gap-1.5">
                <ShieldAlert className="w-4 h-4 shrink-0" />
                <span>Riesgo Granizo: {hailRisk.level.toUpperCase()} ({hailRisk.score.toFixed(0)} pts)</span>
              </div>
              <div className="flex items-center gap-1 text-[11px] font-normal opacity-90">
                <span>{hailRisk.peak_window_local}</span>
                {isHailExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
              </div>
            </button>

            {/* Expandable Anti-Hail Diagnostic Drawer */}
            {isHailExpanded && (
              <div className="mt-2 p-3 rounded-2xl bg-slate-950/90 border border-slate-800 text-[11px] space-y-2 animate-in fade-in duration-200">
                <div className="grid grid-cols-2 gap-2 text-slate-300">
                  <div className="p-1.5 rounded-lg bg-slate-900/80 border border-slate-800">
                    <span className="text-slate-400 block text-[10px]">CAPE Máximo</span>
                    <span className="font-bold text-slate-100">{hailRisk.cape_max} J/kg</span>
                  </div>
                  <div className="p-1.5 rounded-lg bg-slate-900/80 border border-slate-800">
                    <span className="text-slate-400 block text-[10px]">Cizalladura 0-6 km</span>
                    <span className="font-bold text-slate-100">{hailRisk.shear_0_6km_kmh} km/h</span>
                  </div>
                  <div className="p-1.5 rounded-lg bg-slate-900/80 border border-slate-800">
                    <span className="text-slate-400 block text-[10px]">Isoterma 0 °C</span>
                    <span className="font-bold text-slate-100">{hailRisk.freezing_level_m} m</span>
                  </div>
                  <div className="p-1.5 rounded-lg bg-slate-900/80 border border-slate-800">
                    <span className="text-slate-400 block text-[10px]">Tope GOES-19 IR</span>
                    <span className="font-bold text-slate-100">
                      {hailRisk.stage2_details?.min_brightness_temp_c !== null &&
                      hailRisk.stage2_details?.min_brightness_temp_c !== undefined
                        ? `${hailRisk.stage2_details.min_brightness_temp_c} °C`
                        : 'Sin núcleos'}
                    </span>
                  </div>
                </div>
                <p className="text-slate-300 leading-relaxed text-[10.5px] pt-0.5">
                  {hailRisk.explanation}
                </p>
              </div>
            )}
          </div>
        )}

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
                  ? `Rocío ${Math.round(current.dew_point_c)}° · ${getComfortLabel(current.dew_point_c, current.relative_humidity_pct)}`
                  : 'Punto de rocío'}
              </div>
            </div>
          </div>

          {/* Sea Level Pressure QNH with Trend and Delta */}
          <div className="flex items-center gap-2.5 p-2.5 rounded-xl bg-slate-900/60 border border-slate-800/60">
            <div className="p-2 rounded-lg bg-indigo-950/60 text-indigo-400">
              <Gauge className="w-4 h-4" />
            </div>
            <div className="min-w-0">
              <div className="text-[10px] text-slate-400 uppercase font-medium">
                Presión QNH
              </div>
              <div className="font-bold text-slate-200 text-sm truncate">
                {current.pressure_hpa !== null
                  ? `${current.pressure_hpa.toFixed(1)} hPa`
                  : '-- hPa'}
              </div>
              <div className={`text-[10px] ${qnhTrendColor} truncate`}>
                {qnhTrendText}
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
                {isPrecipNow ? (
                  maxPrecipMm > 0 ? (
                    `${maxPrecipMm.toFixed(1)} mm`
                  ) : (
                    <span className="text-sky-300 font-extrabold animate-pulse">Lloviendo ahora</span>
                  )
                ) : maxPrecipMm > 0 ? (
                  `${maxPrecipMm.toFixed(1)} mm`
                ) : (
                  '0 mm'
                )}
              </div>
              <div className="text-[10px] text-slate-400">
                {isPrecipNow ? (
                  <span className="text-sky-400 font-medium">
                    {current.metar_wx_raw ? `Reporte: ${current.metar_wx_raw}` : 'Lloviendo ahora'}
                  </span>
                ) : (
                  `Prob: ${precipProb}%`
                )}
              </div>
            </div>
          </div>
        </div>

        {/* Compact Enriched Row: UV + Sunrise + Sunset */}
        <div className="mt-3 pt-3 border-t border-slate-800/60 flex items-center justify-between text-[11px] text-slate-400 px-1">
          {/* UV (rendered when daytime or > 0) */}
          {showUv && uvVal !== null && uvVal !== undefined ? (
            <div className="flex items-center gap-1.5">
              <Sun className="w-3.5 h-3.5 text-amber-400 shrink-0" />
              <span>
                UV <span className="font-bold text-slate-200">{uvVal.toFixed(1)}</span>
              </span>
              <span className={`px-1.5 py-0.2 rounded text-[10px] font-semibold border ${getUvBadge(uvVal).colorClass}`}>
                {getUvBadge(uvVal).label}
              </span>
            </div>
          ) : (
            <div className="flex items-center gap-1 text-slate-500">
              <Sun className="w-3.5 h-3.5 text-slate-600" />
              <span>Noche</span>
            </div>
          )}

          {/* Sunrise */}
          {current.sunrise_local && (
            <div className="flex items-center gap-1">
              <Sunrise className="w-3.5 h-3.5 text-amber-300 shrink-0" />
              <span>{current.sunrise_local}</span>
            </div>
          )}

          {/* Sunset */}
          {current.sunset_local && (
            <div className="flex items-center gap-1">
              <Sunset className="w-3.5 h-3.5 text-indigo-300 shrink-0" />
              <span>{current.sunset_local}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  );
};
