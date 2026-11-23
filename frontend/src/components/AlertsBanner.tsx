import React, { useState } from 'react';
import { AlertTriangle, AlertOctagon, ChevronDown, ChevronUp } from 'lucide-react';
import type { WeatherAlert } from '../types/weather';

interface AlertsBannerProps {
  alerts: WeatherAlert[];
}

export const AlertsBanner: React.FC<AlertsBannerProps> = ({ alerts }) => {
  const [expandedIndex, setExpandedIndex] = useState<number | null>(null);

  if (!alerts || alerts.length === 0) {
    return null;
  }

  const toggleExpand = (idx: number) => {
    setExpandedIndex(expandedIndex === idx ? null : idx);
  };

  const getAlertStyles = (level: string) => {
    const l = level.toLowerCase();
    if (l === 'rojo' || l === 'red') {
      return {
        bg: 'bg-rose-950/80',
        border: 'border-rose-600',
        text: 'text-rose-100',
        badge: 'bg-rose-600 text-white',
        icon: <AlertOctagon className="w-5 h-5 text-rose-400 shrink-0 animate-bounce" />,
      };
    }
    if (l === 'naranja' || l === 'orange') {
      return {
        bg: 'bg-orange-950/80',
        border: 'border-orange-500',
        text: 'text-orange-100',
        badge: 'bg-orange-500 text-slate-950 font-bold',
        icon: <AlertTriangle className="w-5 h-5 text-orange-400 shrink-0" />,
      };
    }
    // Default: amarillo / yellow
    return {
      bg: 'bg-amber-950/70',
      border: 'border-amber-500/80',
      text: 'text-amber-100',
      badge: 'bg-amber-400 text-slate-950 font-bold',
      icon: <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0" />,
    };
  };

  return (
    <section className="px-4 pt-3 pb-1 max-w-md mx-auto w-full space-y-2">
      {alerts.map((alert, idx) => {
        const styles = getAlertStyles(alert.level);
        const isExpanded = expandedIndex === idx;

        return (
          <div
            key={alert.id || idx}
            className={`rounded-xl border ${styles.border} ${styles.bg} p-3 shadow-lg shadow-black/40 transition-all`}
          >
            <div
              onClick={() => toggleExpand(idx)}
              className="flex items-start justify-between gap-2.5 cursor-pointer select-none"
            >
              <div className="flex items-start gap-2.5">
                {styles.icon}
                <div>
                  <div className="flex items-center gap-1.5 flex-wrap">
                    <span className={`text-[10px] uppercase px-1.5 py-0.5 rounded ${styles.badge}`}>
                      {alert.level}
                    </span>
                    <span className="text-[10px] text-slate-400 font-medium">
                      {alert.source}
                    </span>
                  </div>
                  <h3 className={`font-bold text-sm ${styles.text} mt-0.5 leading-snug`}>
                    {alert.title}
                  </h3>
                </div>
              </div>

              <button
                className="p-1 text-slate-400 hover:text-white"
                aria-label={isExpanded ? 'Contraer' : 'Expandir'}
              >
                {isExpanded ? (
                  <ChevronUp className="w-4 h-4" />
                ) : (
                  <ChevronDown className="w-4 h-4" />
                )}
              </button>
            </div>

            {isExpanded && (
              <div className="mt-2.5 pt-2.5 border-t border-white/10 text-xs text-slate-300 space-y-2 leading-relaxed">
                <p>{alert.description}</p>
                {alert.zones && alert.zones.length > 0 && (
                  <div className="text-[11px] text-slate-400">
                    <span className="font-semibold text-slate-300">Zonas: </span>
                    {alert.zones.join(', ')}
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
};
