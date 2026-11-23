import React, { useState, useEffect } from 'react';
import { X, RefreshCw, Database, Activity } from 'lucide-react';
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
          fetchAccuracy(cityKey),
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
  }, [isOpen, cityKey]);

  if (!isOpen) return null;

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
                Verificación continua contra METAR oficial
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
          {loading ? (
            <div className="flex flex-col items-center justify-center py-10 space-y-2">
              <RefreshCw className="w-6 h-6 text-sky-400 animate-spin" />
              <p className="text-slate-400 text-xs">Consultando base de datos SQLite y fuentes...</p>
            </div>
          ) : (
            <>
              {/* MAE metrics card */}
              <div className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="font-bold text-slate-200">
                    Estación {accuracy?.icao || 'SAOC'} ({accuracy?.city})
                  </span>
                  <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-semibold">
                    {accuracy?.status === 'ok' ? 'Auditoría Activa' : 'Sin historial'}
                  </span>
                </div>

                <div className="grid grid-cols-2 gap-2 text-center">
                  <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                    <div className="text-[10px] text-slate-400 uppercase font-semibold">
                      Muestras Auditadas
                    </div>
                    <div className="text-lg font-black text-sky-400 mt-0.5">
                      {accuracy?.samples_count ?? 0}
                    </div>
                    <div className="text-[10px] text-slate-500">Pares t+0 guardados</div>
                  </div>

                  <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                    <div className="text-[10px] text-slate-400 uppercase font-semibold">
                      MAE Temperatura
                    </div>
                    <div className="text-lg font-black text-emerald-400 mt-0.5">
                      {accuracy?.mae_temperature_c !== null
                        ? `${accuracy?.mae_temperature_c} °C`
                        : '--'}
                    </div>
                    <div className="text-[10px] text-slate-500">Error medio real</div>
                  </div>
                </div>

                {accuracy?.mae_wind_kmh !== null && (
                  <div className="text-center text-[11px] text-slate-400">
                    MAE Viento acumulado:{' '}
                    <span className="text-slate-200 font-bold">
                      {accuracy?.mae_wind_kmh} km/h
                    </span>
                  </div>
                )}
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
                      <div className="font-semibold text-slate-200">Base SQLite Local</div>
                      <div className="text-[10px] text-slate-400 truncate max-w-[200px]">
                        {health?.sources.database.path}
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
