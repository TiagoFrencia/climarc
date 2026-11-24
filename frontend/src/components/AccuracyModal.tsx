import React, { useState, useEffect } from 'react';
import { X, RefreshCw, Database, Activity, Trophy, Gauge } from 'lucide-react';
import type { AccuracyResponse, HealthResponse } from '../types/weather';
import { fetchAccuracy, fetchHealth } from '../services/api';

interface AccuracyModalProps {
  isOpen: boolean;
  onClose: () => void;
  cityKey: string;
}

export const AccuracyModal: React.FC<AccuracyModalProps> = ({
  isOpen,
  onClose,
  cityKey,
}) => {
  const [days, setDays] = useState<7 | 30>(7);
  const [accuracy, setAccuracy] = useState<AccuracyResponse | null>(null);
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    if (!isOpen) return;

    let mounted = true;
    async function loadData() {
      try {
        setLoading(true);
        const [accRes, healthRes] = await Promise.all([
          fetchAccuracy(cityKey, days),
          fetchHealth(),
        ]);
        if (mounted) {
          setAccuracy(accRes);
          setHealth(healthRes);
        }
      } catch (err) {
        console.error('Error fetching accuracy/health:', err);
      } finally {
        if (mounted) setLoading(false);
      }
    }

    loadData();
    return () => {
      mounted = false;
    };
  }, [isOpen, cityKey, days]);

  if (!isOpen) return null;

  // Filter leaderboard: keep AIFS only if it has samples > 0
  const leaderboard = (accuracy?.leaderboard || []).filter(
    (m) => m.model_source !== 'ecmwf_aifs025' || m.samples_count > 0
  );

  const currentWeights = accuracy?.current_weights || {
    wrf_smn: 0.35,
    ecmwf_ifs025: 0.35,
    icon_seamless: 0.15,
    gfs_seamless: 0.15,
  };

  const isAdaptiveActive = Boolean(accuracy?.adaptive_weights_active);
  const samplesAcc =
    accuracy?.progress_samples?.samples_accumulated ??
    accuracy?.samples_count ??
    0;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div className="relative w-full max-w-md bg-[#111827] border border-slate-800 rounded-3xl shadow-2xl p-5 overflow-hidden max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-emerald-950/80 text-emerald-400 border border-emerald-800/60">
              <Activity className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100">
                Auditoría de Precisión Real
              </h2>
              <p className="text-xs text-slate-400">
                Verificación continua contra METAR oficial ({accuracy?.icao || 'SAOC'})
              </p>
            </div>
          </div>
          <button
            onClick={onClose}
            className="p-1.5 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-colors"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="overflow-y-auto py-4 space-y-4 pr-1 text-xs text-slate-300">
          {/* Quick 7d / 30d selector */}
          <div className="flex bg-slate-900 p-0.5 rounded-xl border border-slate-800 text-xs w-full">
            <button
              type="button"
              onClick={() => setDays(7)}
              className={`flex-1 py-1.5 rounded-lg font-bold transition-all ${
                days === 7
                  ? 'bg-sky-500 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Últimos 7 días
            </button>
            <button
              type="button"
              onClick={() => setDays(30)}
              className={`flex-1 py-1.5 rounded-lg font-bold transition-all ${
                days === 30
                  ? 'bg-sky-500 text-white shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Últimos 30 días
            </button>
          </div>

          {loading ? (
            <div className="flex flex-col items-center justify-center py-10 space-y-2">
              <RefreshCw className="w-6 h-6 text-sky-400 animate-spin" />
              <p className="text-slate-400 text-xs">Consultando base de datos y fuentes...</p>
            </div>
          ) : (
            <>
              {/* Weighting Engine Badge Card */}
              <div className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-2.5">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200 flex items-center gap-1.5">
                    <Gauge className="w-3.5 h-3.5 text-sky-400" />
                    Motor de Ponderación
                  </span>
                  <span
                    className={`text-[10.5px] px-2.5 py-0.5 rounded-full font-bold border ${
                      isAdaptiveActive
                        ? 'bg-emerald-950/90 text-emerald-300 border-emerald-700/80'
                        : 'bg-amber-950/80 text-amber-300 border-amber-700/80'
                    }`}
                  >
                    {isAdaptiveActive
                      ? '⚡ Pesos Adaptativos Activos'
                      : `Pesos Estáticos (${samplesAcc}/48 hs para activar)`}
                  </span>
                </div>

                {/* Percentage Distribution Pill */}
                <div className="p-2 rounded-xl bg-slate-950/60 border border-slate-800 text-[11px] flex items-center justify-around text-center text-slate-300">
                  <div>
                    <span className="text-[10px] text-slate-500 block">WRF 4km</span>
                    <span className="font-bold text-sky-400">
                      {Math.round((currentWeights.wrf_smn ?? 0.35) * 100)}%
                    </span>
                  </div>
                  <div className="text-slate-700">|</div>
                  <div>
                    <span className="text-[10px] text-slate-500 block">ECMWF IFS</span>
                    <span className="font-bold text-sky-400">
                      {Math.round((currentWeights.ecmwf_ifs025 ?? 0.35) * 100)}%
                    </span>
                  </div>
                  <div className="text-slate-700">|</div>
                  <div>
                    <span className="text-[10px] text-slate-500 block">ICON</span>
                    <span className="font-bold text-sky-400">
                      {Math.round((currentWeights.icon_seamless ?? 0.15) * 100)}%
                    </span>
                  </div>
                  <div className="text-slate-700">|</div>
                  <div>
                    <span className="text-[10px] text-slate-500 block">GFS</span>
                    <span className="font-bold text-sky-400">
                      {Math.round((currentWeights.gfs_seamless ?? 0.15) * 100)}%
                    </span>
                  </div>
                  {currentWeights.ecmwf_aifs025 !== undefined && (
                    <>
                      <div className="text-slate-700">|</div>
                      <div>
                        <span className="text-[10px] text-slate-500 block">AIFS</span>
                        <span className="font-bold text-purple-400">
                          {Math.round(currentWeights.ecmwf_aifs025 * 100)}%
                        </span>
                      </div>
                    </>
                  )}
                </div>
              </div>

              {/* Leaderboard Table Card */}
              <div className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-2.5">
                <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                  <div className="flex items-center gap-1.5">
                    <Trophy className="w-4 h-4 text-amber-400" />
                    <span className="font-bold text-slate-100 text-xs">
                      Ranking de Modelos en Río Cuarto
                    </span>
                  </div>
                  <span className="text-[10px] text-slate-400 font-medium">
                    {days} días acumulados
                  </span>
                </div>

                <div className="overflow-x-auto">
                  <table className="w-full text-left text-xs">
                    <thead>
                      <tr className="border-b border-slate-800/80 text-[10px] text-slate-400 uppercase tracking-wider">
                        <th className="pb-1.5 font-semibold">Modelo</th>
                        <th className="pb-1.5 font-semibold text-right">MAE Temp</th>
                        <th className="pb-1.5 font-semibold text-right">MAE Viento</th>
                        <th className="pb-1.5 font-semibold text-right">Muestras</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-800/40">
                      {leaderboard.length > 0 ? (
                        leaderboard.map((m, idx) => (
                          <tr key={m.model_source} className="hover:bg-slate-800/30 transition-colors">
                            <td className="py-1.5 font-semibold flex items-center gap-1.5">
                              <span className="text-[10px] text-slate-500 w-3">{idx + 1}</span>
                              <span
                                className={
                                  m.model_source === 'ensemble_ml'
                                    ? 'text-sky-400 font-bold'
                                    : 'text-slate-200'
                                }
                              >
                                {m.display_name}
                              </span>
                            </td>
                            <td className="py-1.5 text-right font-bold text-emerald-400">
                              {m.mae_temperature_c !== null
                                ? `${m.mae_temperature_c} °C`
                                : '--'}
                            </td>
                            <td className="py-1.5 text-right text-slate-300">
                              {m.mae_wind_kmh !== null
                                ? `${m.mae_wind_kmh} km/h`
                                : '--'}
                            </td>
                            <td className="py-1.5 text-right text-slate-400">
                              {m.samples_count}
                            </td>
                          </tr>
                        ))
                      ) : (
                        <tr>
                          <td colSpan={4} className="py-4 text-center text-slate-500">
                            Aún no hay suficientes muestras auditadas en este período.
                          </td>
                        </tr>
                      )}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Data Sources Health Status */}
              <div>
                <h3 className="font-bold text-slate-200 mb-2 flex items-center gap-1.5">
                  <Database className="w-3.5 h-3.5 text-sky-400" />
                  Estado de Fuentes y Enlaces
                </h3>

                <div className="space-y-1.5">
                  {/* WRF */}
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-slate-200">Modelo WRF-SMN 4km</div>
                      <div className="text-[10px] text-slate-400">
                        {health?.sources.wrf.available
                          ? `Corrida: ${health.sources.wrf.run_time_utc?.slice(0, 16).replace('T', ' ')} (${health.sources.wrf.age_hours?.toFixed(1)} h)`
                          : 'No disponible / fallback Open-Meteo'}
                      </div>
                    </div>
                    <span
                      className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
                        health?.sources.wrf.available
                          ? 'bg-emerald-950 text-emerald-400 border border-emerald-800'
                          : 'bg-amber-950 text-amber-400 border border-amber-800'
                      }`}
                    >
                      {health?.sources.wrf.available ? 'En línea' : 'Fallback'}
                    </span>
                  </div>

                  {/* METAR */}
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-slate-200">METAR Aeropuertos (SAOC / SACO)</div>
                      <div className="text-[10px] text-slate-400">
                        {health?.sources.metar.saoc_records_cached ?? 0} obs SAOC · {health?.sources.metar.saco_records_cached ?? 0} obs SACO
                      </div>
                    </div>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                      Conectado
                    </span>
                  </div>

                  {/* SMN Official */}
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-slate-200">Alertas SMN Oficial</div>
                      <div className="text-[10px] text-slate-400">
                        {health?.sources.smn_official.jwt_configured
                          ? 'Token JWT activo'
                          : 'Degradación elegante + Alertas locales'}
                      </div>
                    </div>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700">
                      {health?.sources.smn_official.status}
                    </span>
                  </div>

                  {/* Database */}
                  <div className="p-2.5 rounded-xl bg-slate-900 border border-slate-800/80 flex items-center justify-between">
                    <div>
                      <div className="font-semibold text-slate-200">
                        {health?.sources.database.engine === 'postgresql'
                          ? 'PostgreSQL (Neon · Persistente)'
                          : 'Base SQLite Local'}
                      </div>
                      <div className="text-[10px] text-slate-400 truncate max-w-[200px]">
                        {health?.sources.database.engine === 'postgresql'
                          ? (health?.sources.database.target || 'Pooler externo persistente')
                          : (health?.sources.database.path || 'climarc.db')}
                      </div>
                    </div>
                    <span className="text-[10px] font-bold px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                      {health?.sources.database.status}
                    </span>
                  </div>
                </div>
              </div>
            </>
          )}
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="w-full py-2 bg-slate-800 hover:bg-slate-700 active:scale-95 text-slate-200 font-semibold rounded-xl transition-all"
          >
            Cerrar
          </button>
        </div>
      </div>
    </div>
  );
};
