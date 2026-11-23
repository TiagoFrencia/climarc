import React from 'react';
import { RefreshCw, Navigation, Activity, Settings } from 'lucide-react';
import type { CityConfig, CurrentWeather } from '../types/weather';
import { formatAgeMinutes } from '../utils/weatherFormatters';

interface HeaderProps {
  currentCity: string;
  cityConfig?: CityConfig;
  currentWeather?: CurrentWeather;
  isLoading: boolean;
  onSelectCity: (cityKey: string) => void;
  onUseGps: () => void;
  onRefresh: () => void;
  onOpenAccuracy: () => void;
  onOpenSettings: () => void;
  gpsActive: boolean;
}

export const Header: React.FC<HeaderProps> = ({
  currentCity,
  cityConfig,
  currentWeather,
  isLoading,
  onSelectCity,
  onUseGps,
  onRefresh,
  onOpenAccuracy,
  onOpenSettings,
  gpsActive,
}) => {
  const isNowcast = currentWeather?.nowcast_blended === true;
  const stationName = currentWeather?.metar_station || cityConfig?.icao || 'SAOC';
  const ageMinutes = currentWeather?.metar_age_minutes;

  return (
    <header className="sticky top-0 z-30 bg-[#0B1120]/90 backdrop-blur-md border-b border-slate-800/80 px-4 py-3 transition-colors">
      <div className="max-w-md mx-auto flex flex-col gap-2.5">
        {/* Top row: Brand + Action buttons */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-sky-400 to-blue-600 flex items-center justify-center shadow-lg shadow-sky-500/20">
              <span className="text-white font-extrabold text-base tracking-tighter">RC</span>
            </div>
            <div>
              <h1 className="font-bold text-base text-slate-100 tracking-tight leading-none">
                Clima<span className="text-sky-400">RC</span>
              </h1>
              <span className="text-[10px] text-slate-400 tracking-wider uppercase font-semibold">
                Nowcasting + IA
              </span>
            </div>
          </div>

          <div className="flex items-center gap-1.5">
            {/* Accuracy modal trigger */}
            <button
              onClick={onOpenAccuracy}
              title="Auditoría de Precisión Real"
              className="p-2 text-slate-400 hover:text-sky-400 rounded-lg hover:bg-slate-800/60 active:scale-95 transition-all"
              aria-label="Precisión Real"
            >
              <Activity className="w-4 h-4" />
            </button>

            {/* Settings modal trigger */}
            <button
              onClick={onOpenSettings}
              title="Ajustes y Telegram"
              className="p-2 text-slate-400 hover:text-sky-400 rounded-lg hover:bg-slate-800/60 active:scale-95 transition-all"
              aria-label="Ajustes"
            >
              <Settings className="w-4 h-4" />
            </button>

            {/* Refresh button with spinner */}
            <button
              onClick={onRefresh}
              disabled={isLoading}
              title="Forzar actualización en vivo"
              className="p-2 text-slate-200 bg-slate-800/80 hover:bg-slate-700/80 active:scale-95 rounded-lg border border-slate-700/60 transition-all disabled:opacity-60"
              aria-label="Actualizar datos"
            >
              <RefreshCw className={`w-4 h-4 text-sky-400 ${isLoading ? 'animate-spin' : ''}`} />
            </button>
          </div>
        </div>

        {/* Location selector pills */}
        <div className="flex items-center justify-between gap-1.5 bg-slate-900/90 p-1 rounded-xl border border-slate-800">
          <button
            onClick={() => onSelectCity('rio_cuarto')}
            className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all text-center ${
              !gpsActive && currentCity === 'rio_cuarto'
                ? 'bg-sky-500 text-white font-semibold shadow-sm shadow-sky-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            Río Cuarto
          </button>

          <button
            onClick={() => onSelectCity('cordoba')}
            className={`flex-1 py-1.5 px-2 rounded-lg text-xs font-medium transition-all text-center ${
              !gpsActive && currentCity === 'cordoba'
                ? 'bg-sky-500 text-white font-semibold shadow-sm shadow-sky-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            Córdoba
          </button>

          <button
            onClick={onUseGps}
            title="Usar ubicación GPS del teléfono"
            className={`flex items-center justify-center gap-1 py-1.5 px-3 rounded-lg text-xs font-medium transition-all ${
              gpsActive
                ? 'bg-emerald-500 text-white font-semibold shadow-sm shadow-emerald-500/30'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
            }`}
          >
            <Navigation className="w-3 h-3" />
            <span>GPS</span>
          </button>
        </div>

        {/* Data Source Truth Pill */}
        <div className="flex items-center justify-between px-1">
          <div className="flex items-center gap-1.5">
            <span
              className={`w-2 h-2 rounded-full ${
                isNowcast ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
              }`}
            />
            {isNowcast ? (
              <span className="text-[11px] font-medium text-emerald-400">
                {stationName} en vivo · {formatAgeMinutes(ageMinutes)}
              </span>
            ) : (
              <span className="text-[11px] font-medium text-slate-400">
                Ensamble + IA ({stationName} {formatAgeMinutes(ageMinutes)})
              </span>
            )}
          </div>

          {currentWeather?.confidence && (
            <span
              className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                currentWeather.confidence === 'alta'
                  ? 'bg-emerald-950/60 text-emerald-300 border-emerald-800/60'
                  : currentWeather.confidence === 'media'
                  ? 'bg-amber-950/60 text-amber-300 border-amber-800/60'
                  : 'bg-rose-950/60 text-rose-300 border-rose-800/60'
              }`}
            >
              Confianza {currentWeather.confidence.toUpperCase()}
            </span>
          )}
        </div>
      </div>
    </header>
  );
};
