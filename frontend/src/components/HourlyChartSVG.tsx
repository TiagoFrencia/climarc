import React from 'react';
import type { HourlyForecast } from '../types/weather';
import { formatLocalHour } from '../utils/weatherFormatters';

interface HourlyChartSVGProps {
  hourly: HourlyForecast[];
}

export const HourlyChartSVG: React.FC<HourlyChartSVGProps> = ({ hourly }) => {
  // Use first 24 hours
  const steps = hourly.slice(0, 24);
  if (steps.length === 0) return null;

  // Chart dimensions & margins
  const width = 500;
  const height = 185;
  const padLeft = 32;
  const padRight = 24;
  const padTop = 32;
  const tempChartBottom = 120;
  const precipBaseY = 162;
  const precipMaxHeight = 26;

  // Extract values and models spread
  const points = steps.map((step, idx) => {
    const x = padLeft + (idx / Math.max(1, steps.length - 1)) * (width - padLeft - padRight);
    const temp = step.temperature_c;

    // Model temperatures for uncertainty band
    const m = step.models_comparison || {};
    const modelTemps: number[] = [
      m.ecmwf?.temperature_c,
      m.icon?.temperature_c,
      m.gfs?.temperature_c,
      m.wrf?.temperature_c,
      m.aifs?.temperature_c,
    ].filter((v): v is number => v !== undefined && v !== null);

    const minModel = modelTemps.length > 0 ? Math.min(...modelTemps, temp) : temp;
    const maxModel = modelTemps.length > 0 ? Math.max(...modelTemps, temp) : temp;

    return {
      idx,
      x,
      temp,
      minModel,
      maxModel,
      precip: step.precip_mm || 0.0,
      timestamp: step.timestamp_utc,
    };
  });

  // Calculate scales
  const allTemps = points.flatMap((p) => [p.temp, p.minModel, p.maxModel]);
  const minTemp = Math.floor(Math.min(...allTemps) - 1.5);
  const maxTemp = Math.ceil(Math.max(...allTemps) + 1.5);
  const tempRange = Math.max(1.0, maxTemp - minTemp);

  const getYForTemp = (t: number) => {
    const norm = (t - minTemp) / tempRange;
    return tempChartBottom - norm * (tempChartBottom - padTop);
  };

  const maxPrecip = Math.max(...points.map((p) => p.precip), 2.0);

  // Generate smooth SVG path for temperature curve (Catmull-Rom or cubic bezier)
  const linePath = points.reduce((acc, p, i) => {
    const y = getYForTemp(p.temp);
    if (i === 0) return `M ${p.x.toFixed(1)} ${y.toFixed(1)}`;
    const prev = points[i - 1];
    const prevY = getYForTemp(prev.temp);
    const cpX1 = (prev.x + (p.x - prev.x) * 0.5).toFixed(1);
    const cpX2 = (prev.x + (p.x - prev.x) * 0.5).toFixed(1);
    return `${acc} C ${cpX1} ${prevY.toFixed(1)}, ${cpX2} ${y.toFixed(1)}, ${p.x.toFixed(1)} ${y.toFixed(1)}`;
  }, '');

  // Generate uncertainty band area path:
  // Forward along maxModel curve, then backward along minModel curve
  const upperPath = points.reduce((acc, p, i) => {
    const y = getYForTemp(p.maxModel);
    if (i === 0) return `M ${p.x.toFixed(1)} ${y.toFixed(1)}`;
    const prev = points[i - 1];
    const prevY = getYForTemp(prev.maxModel);
    const cpX = (prev.x + (p.x - prev.x) * 0.5).toFixed(1);
    return `${acc} C ${cpX} ${prevY.toFixed(1)}, ${cpX} ${y.toFixed(1)}, ${p.x.toFixed(1)} ${y.toFixed(1)}`;
  }, '');

  const lowerPoints = [...points].reverse();
  const lowerPath = lowerPoints.reduce((acc, p, i) => {
    const y = getYForTemp(p.minModel);
    if (i === 0) return `${acc} L ${p.x.toFixed(1)} ${y.toFixed(1)}`;
    const prev = lowerPoints[i - 1];
    const prevY = getYForTemp(prev.minModel);
    const cpX = (prev.x + (p.x - prev.x) * 0.5).toFixed(1);
    return `${acc} C ${cpX} ${prevY.toFixed(1)}, ${cpX} ${y.toFixed(1)}, ${p.x.toFixed(1)} ${y.toFixed(1)}`;
  }, '');

  const uncertaintyAreaPath = `${upperPath} ${lowerPath} Z`;

  return (
    <div className="w-full bg-[#111827] rounded-2xl p-3 border border-slate-800 shadow-md">
      {/* Legend & Summary */}
      <div className="flex items-center justify-between text-[10px] text-slate-400 mb-2 px-1">
        <div className="flex items-center gap-3 font-medium">
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-1 rounded-full bg-sky-400 inline-block" />
            <span className="text-slate-200 font-semibold">Ensamble + IA</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2.5 h-2 rounded bg-sky-500/25 border border-sky-400/40 inline-block" />
            <span>Dispersión modelos</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-sm bg-blue-500 inline-block" />
            <span>Lluvia (mm)</span>
          </div>
        </div>
        <span className="text-slate-500 font-mono">24h</span>
      </div>

      <div className="w-full overflow-hidden">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          className="w-full h-auto select-none"
          style={{ maxHeight: '190px' }}
        >
          <defs>
            {/* Uncertainty gradient */}
            <linearGradient id="uncertaintyGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38BDF8" stopOpacity="0.25" />
              <stop offset="100%" stopColor="#0284C7" stopOpacity="0.08" />
            </linearGradient>

            {/* Line glow filter */}
            <linearGradient id="tempLineGrad" x1="0" y1="0" x2="1" y2="0">
              <stop offset="0%" stopColor="#38BDF8" />
              <stop offset="50%" stopColor="#22D3EE" />
              <stop offset="100%" stopColor="#818CF8" />
            </linearGradient>

            <linearGradient id="precipGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38BDF8" stopOpacity="0.95" />
              <stop offset="100%" stopColor="#1E40AF" stopOpacity="0.75" />
            </linearGradient>
          </defs>

          {/* Background horizontal grid lines */}
          <line
            x1={padLeft}
            y1={padTop}
            x2={width - padRight}
            y2={padTop}
            stroke="#1E293B"
            strokeDasharray="3 3"
            strokeWidth="0.8"
          />
          <line
            x1={padLeft}
            y1={(padTop + tempChartBottom) / 2}
            x2={width - padRight}
            y2={(padTop + tempChartBottom) / 2}
            stroke="#1E293B"
            strokeDasharray="3 3"
            strokeWidth="0.8"
          />
          <line
            x1={padLeft}
            y1={tempChartBottom}
            x2={width - padRight}
            y2={tempChartBottom}
            stroke="#334155"
            strokeWidth="1"
          />

          {/* 1. Uncertainty Band Area (Multi-model dispersion) */}
          <path d={uncertaintyAreaPath} fill="url(#uncertaintyGrad)" />

          {/* 2. Main Temperature Curve */}
          <path
            d={linePath}
            fill="none"
            stroke="url(#tempLineGrad)"
            strokeWidth="2.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          />

          {/* 3. Precipitation Bars (at bottom) */}
          {points.map((p) => {
            if (p.precip <= 0) return null;
            const barH = Math.min(
              precipMaxHeight,
              Math.max(4, (p.precip / maxPrecip) * precipMaxHeight)
            );
            const barY = precipBaseY - barH;
            return (
              <g key={`bar-${p.idx}`}>
                <rect
                  x={p.x - 3.5}
                  y={barY}
                  width="7"
                  height={barH}
                  rx="2"
                  fill="url(#precipGrad)"
                />
                {p.precip >= 0.5 && (
                  <text
                    x={p.x}
                    y={barY - 2}
                    textAnchor="middle"
                    fontSize="8"
                    fill="#38BDF8"
                    fontWeight="bold"
                  >
                    {p.precip.toFixed(1)}
                  </text>
                )}
              </g>
            );
          })}

          {/* 4. Temperature Dots & Value Labels every 3 hours */}
          {points.map((p) => {
            const isLabelStep = p.idx % 3 === 0 || p.idx === points.length - 1;
            const y = getYForTemp(p.temp);
            const timeLabel = p.idx === 0 ? 'Ahora' : formatLocalHour(p.timestamp);

            return (
              <g key={`pt-${p.idx}`}>
                {isLabelStep && (
                  <>
                    {/* Vertical tick */}
                    <line
                      x1={p.x}
                      y1={tempChartBottom}
                      x2={p.x}
                      y2={precipBaseY + 2}
                      stroke="#1E293B"
                      strokeDasharray="2 2"
                      strokeWidth="0.8"
                    />

                    {/* Point on curve */}
                    <circle cx={p.x} cy={y} r="3" fill="#38BDF8" stroke="#0B1120" strokeWidth="1.5" />

                    {/* Temp Label above curve */}
                    <text
                      x={p.x}
                      y={y - 7}
                      textAnchor="middle"
                      fontSize="9.5"
                      fill="#F8FAFC"
                      fontWeight="bold"
                    >
                      {Math.round(p.temp)}°
                    </text>

                    {/* Time Label on X axis */}
                    <text
                      x={p.x}
                      y={height - 5}
                      textAnchor="middle"
                      fontSize="9"
                      fill="#94A3B8"
                      fontWeight="500"
                    >
                      {timeLabel}
                    </text>
                  </>
                )}
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
};
