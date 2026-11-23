import React, { useState } from 'react';
import { ChevronDown, ChevronUp, Cpu, CheckCircle2 } from 'lucide-react';
import type { HourlyForecast, MLMetadata } from '../types/weather';
import { formatLocalHour } from '../utils/weatherFormatters';

interface ModelsTransparencyProps {
  hourly: HourlyForecast[];
  mlMetadata?: MLMetadata;
  wrfAvailable: boolean;
}

export const ModelsTransparency: React.FC<ModelsTransparencyProps> = ({
  hourly,
  mlMetadata,
  wrfAvailable,
}) => {
  const [isOpen, setIsOpen] = useState<boolean>(false);
  const [selectedMetric, setSelectedMetric] = useState<'temp' | 'precip' | 'wind'>('temp');

  const steps12h = hourly.slice(0, 12);

  const hasAifsData = steps12h.some((step) => {
    const aifs = step.models_comparison?.aifs;
    if (!aifs) return false;
    return (
      (aifs.temperature_c !== undefined && aifs.temperature_c !== null) ||
      (aifs.precip_mm !== undefined && aifs.precip_mm !== null) ||
      (aifs.wind_speed_kmh !== undefined && aifs.wind_speed_kmh !== null)
    );
  });

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl overflow-hidden transition-all">
        {/* Accordion Toggle Header */}
        <button
          onClick={() => setIsOpen(!isOpen)}
          className="w-full p-4 flex items-center justify-between text-left hover:bg-slate-800/40 transition-colors"
          aria-expanded={isOpen}
        >
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-cyan-950/70 text-cyan-400 border border-cyan-800/50">
              <Cpu className="w-4 h-4" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-slate-100 flex items-center gap-1.5">
                Transparencia de Modelos e IA
              </h3>
              <p className="text-[11px] text-slate-400">
                WRF-SMN 4km {wrfAvailable ? '(Activo)' : '(Offline)'} · ECMWF · ICON · GFS · LightGBM
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
              {isOpen ? 'Ocultar' : 'Comparar'}
            </span>
            {isOpen ? (
              <ChevronUp className="w-4 h-4 text-slate-400" />
            ) : (
              <ChevronDown className="w-4 h-4 text-slate-400" />
            )}
          </div>
        </button>

        {isOpen && (
          <div className="p-4 pt-0 border-t border-slate-800/80 space-y-4">
            {/* ML Bias Corrector Metrics Card */}
            {mlMetadata && mlMetadata.available && (
              <div className="p-3 rounded-2xl bg-gradient-to-br from-cyan-950/40 to-slate-900/80 border border-cyan-800/40 text-xs space-y-2.5">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5 font-bold text-cyan-300">
                    <CheckCircle2 className="w-3.5 h-3.5 text-cyan-400" />
                    <span>Corrector IA LightGBM ({mlMetadata.station})</span>
                  </div>
                  <span className="text-[10px] font-semibold text-slate-400">
                    {mlMetadata.samples_matched?.toLocaleString()} hs auditadas
                  </span>
                </div>

                <p className="text-[11px] text-slate-300 leading-relaxed">
                  Entrenado sobre datos reales del aeródromo de Río Cuarto (SAOC). Reduce errores sistemáticos locales diurnos y nocturnos.
                </p>

                <div className="grid grid-cols-2 gap-2 text-center pt-1">
                  <div className="p-2 rounded-xl bg-slate-900/80 border border-slate-800">
                    <div className="text-[10px] text-slate-400 font-medium">Temperatura (MAE)</div>
                    <div className="text-sm font-extrabold text-cyan-300 mt-0.5">
                      {mlMetadata.temperature_metrics?.mae_before ?? 1.22}° → {mlMetadata.temperature_metrics?.mae_after ?? 1.14}°C
                    </div>
                    <div className="text-[10px] text-emerald-400 font-bold">
                      +{mlMetadata.temperature_metrics?.improvement_pct ?? 6.6}% mejora (+10.5% diurno)
                    </div>
                  </div>

                  <div className="p-2 rounded-xl bg-slate-900/80 border border-slate-800">
                    <div className="text-[10px] text-slate-400 font-medium">Viento (MAE)</div>
                    <div className="text-sm font-extrabold text-cyan-300 mt-0.5">
                      {mlMetadata.wind_metrics?.mae_before ?? 5.0} → {mlMetadata.wind_metrics?.mae_after ?? 4.5} km/h
                    </div>
                    <div className="text-[10px] text-emerald-400 font-bold">
                      +{mlMetadata.wind_metrics?.improvement_pct ?? 9.7}% mejora (+18.1% diurno)
                    </div>
                  </div>
                </div>
              </div>
            )}

            {/* Metric Switcher */}
            <div>
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-slate-300">
                  Comparativa a 12 horas
                </span>
                <div className="flex rounded-lg bg-slate-900 p-0.5 border border-slate-800 text-[11px]">
                  <button
                    onClick={() => setSelectedMetric('temp')}
                    className={`px-2 py-0.5 rounded-md font-medium transition-all ${
                      selectedMetric === 'temp'
                        ? 'bg-sky-500 text-white font-semibold'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Temp
                  </button>
                  <button
                    onClick={() => setSelectedMetric('precip')}
                    className={`px-2 py-0.5 rounded-md font-medium transition-all ${
                      selectedMetric === 'precip'
                        ? 'bg-sky-500 text-white font-semibold'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Lluvia
                  </button>
                  <button
                    onClick={() => setSelectedMetric('wind')}
                    className={`px-2 py-0.5 rounded-md font-medium transition-all ${
                      selectedMetric === 'wind'
                        ? 'bg-sky-500 text-white font-semibold'
                        : 'text-slate-400 hover:text-slate-200'
                    }`}
                  >
                    Viento
                  </button>
                </div>
              </div>

              {/* Responsive comparison table */}
              <div className="overflow-x-auto rounded-xl border border-slate-800">
                <table className="w-full text-xs text-left">
                  <thead className="bg-slate-900 text-slate-400 text-[10px] uppercase font-semibold border-b border-slate-800">
                    <tr>
                      <th className="py-2 px-2.5">Hora</th>
                      <th className="py-2 px-2 text-center text-cyan-300">
                        WRF-SMN
                      </th>
                      <th className="py-2 px-2 text-center">ECMWF</th>
                      <th className="py-2 px-2 text-center">ICON</th>
                      <th className="py-2 px-2 text-center">GFS</th>
                      {hasAifsData && (
                        <th className="py-2 px-2 text-center text-purple-300">AIFS</th>
                      )}
                      <th className="py-2 px-1.5 text-center text-sky-400 font-bold whitespace-nowrap">
                        Final+IA
                      </th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800/60 text-slate-200">
                    {steps12h.map((step, idx) => {
                      const hStr = idx === 0 ? 'Ahora' : formatLocalHour(step.timestamp_utc);
                      const m = step.models_comparison;

                      const valWrf =
                        selectedMetric === 'temp'
                          ? m.wrf?.temperature_c !== undefined && m.wrf?.temperature_c !== null
                            ? `${m.wrf.temperature_c.toFixed(1)}°`
                            : '--'
                          : selectedMetric === 'precip'
                          ? m.wrf?.precip_mm !== undefined && m.wrf?.precip_mm !== null
                            ? `${m.wrf.precip_mm.toFixed(1)}`
                            : '--'
                          : m.wrf?.wind_speed_kmh !== undefined && m.wrf?.wind_speed_kmh !== null
                          ? `${Math.round(m.wrf.wind_speed_kmh)}`
                          : '--';

                      const valEc =
                        selectedMetric === 'temp'
                          ? m.ecmwf?.temperature_c !== undefined && m.ecmwf?.temperature_c !== null
                            ? `${m.ecmwf.temperature_c.toFixed(1)}°`
                            : '--'
                          : selectedMetric === 'precip'
                          ? m.ecmwf?.precip_mm !== undefined && m.ecmwf?.precip_mm !== null
                            ? `${m.ecmwf.precip_mm.toFixed(1)}`
                            : '--'
                          : m.ecmwf?.wind_speed_kmh !== undefined && m.ecmwf?.wind_speed_kmh !== null
                          ? `${Math.round(m.ecmwf.wind_speed_kmh)}`
                          : '--';

                      const valIcon =
                        selectedMetric === 'temp'
                          ? m.icon?.temperature_c !== undefined && m.icon?.temperature_c !== null
                            ? `${m.icon.temperature_c.toFixed(1)}°`
                            : '--'
                          : selectedMetric === 'precip'
                          ? m.icon?.precip_mm !== undefined && m.icon?.precip_mm !== null
                            ? `${m.icon.precip_mm.toFixed(1)}`
                            : '--'
                          : m.icon?.wind_speed_kmh !== undefined && m.icon?.wind_speed_kmh !== null
                          ? `${Math.round(m.icon.wind_speed_kmh)}`
                          : '--';

                      const valGfs =
                        selectedMetric === 'temp'
                          ? m.gfs?.temperature_c !== undefined && m.gfs?.temperature_c !== null
                            ? `${m.gfs.temperature_c.toFixed(1)}°`
                            : '--'
                          : selectedMetric === 'precip'
                          ? m.gfs?.precip_mm !== undefined && m.gfs?.precip_mm !== null
                            ? `${m.gfs.precip_mm.toFixed(1)}`
                            : '--'
                          : m.gfs?.wind_speed_kmh !== undefined && m.gfs?.wind_speed_kmh !== null
                          ? `${Math.round(m.gfs.wind_speed_kmh)}`
                          : '--';

                      const valAifs =
                        selectedMetric === 'temp'
                          ? m.aifs?.temperature_c !== undefined && m.aifs?.temperature_c !== null
                            ? `${m.aifs.temperature_c.toFixed(1)}°`
                            : '--'
                          : selectedMetric === 'precip'
                          ? m.aifs?.precip_mm !== undefined && m.aifs?.precip_mm !== null
                            ? `${m.aifs.precip_mm.toFixed(1)}`
                            : '--'
                          : m.aifs?.wind_speed_kmh !== undefined && m.aifs?.wind_speed_kmh !== null
                          ? `${Math.round(m.aifs.wind_speed_kmh)}`
                          : '--';

                      const valEns =
                        selectedMetric === 'temp'
                          ? `${step.temperature_c.toFixed(1)}°`
                          : selectedMetric === 'precip'
                          ? `${step.precip_mm.toFixed(1)}`
                          : `${Math.round(step.wind_speed_kmh)}`;

                      return (
                        <tr key={step.timestamp_utc || idx} className="hover:bg-slate-800/30">
                          <td className="py-1.5 px-2.5 font-medium text-slate-400">
                            {hStr}
                          </td>
                          <td className="py-1.5 px-2 text-center text-cyan-300 font-medium">
                            {valWrf}
                          </td>
                          <td className="py-1.5 px-2 text-center text-slate-300">
                            {valEc}
                          </td>
                          <td className="py-1.5 px-2 text-center text-slate-300">
                            {valIcon}
                          </td>
                          <td className="py-1.5 px-2 text-center text-slate-300">
                            {valGfs}
                          </td>
                          {hasAifsData && (
                            <td className="py-1.5 px-2 text-center text-purple-300 font-medium">
                              {valAifs}
                            </td>
                          )}
                          <td className="py-1.5 px-2 text-center font-bold text-sky-400 bg-sky-950/20">
                            {valEns}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};
