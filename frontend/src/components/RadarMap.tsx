import React, { useState, useEffect, useRef, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import {
  Play,
  Pause,
  RotateCcw,
  AlertTriangle,
  Radio,
  Loader2,
  CloudLightning,
  Maximize2,
  Minimize2,
  Zap,
  Gauge,
  Wifi,
  WifiOff,
} from 'lucide-react';
import type { RadarResponse, RadarFrame, SentinelStationSummary } from '../types/weather';
import { fetchRadar } from '../services/api';

interface RadarMapProps {
  lat: number;
  lon: number;
  cityName: string;
  sentinels?: SentinelStationSummary[];
}

type LayerMode = 'radar' | 'satellite';

// Controller to smoothly pan map on city switch or fullscreen toggle
function MapViewController({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, zoom, { animate: true });
    // Invalidate map size after any transition
    const timer = setTimeout(() => {
      map.invalidateSize();
    }, 200);
    return () => clearTimeout(timer);
  }, [center, zoom, map]);
  return null;
}

// Sleek discreet center pin
const cityMarkerIcon = L.divIcon({
  className: 'custom-radar-pin',
  html: `
    <div style="position: relative; width: 14px; height: 14px; display: flex; align-items: center; justify-content: center;">
      <div style="position: absolute; width: 14px; height: 14px; background: rgba(56, 189, 248, 0.25); border-radius: 50%; animation: ping 2s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>
      <div style="width: 8px; height: 8px; background: rgba(11, 17, 32, 0.7); border: 1.5px solid #38BDF8; border-radius: 50%; box-shadow: 0 0 6px rgba(56, 189, 248, 0.8);"></div>
    </div>
  `,
  iconSize: [14, 14],
  iconAnchor: [7, 7],
});

// Create custom Sentinel Station Marker Icon with live temp and wind arrow
function createSentinelIcon(s: SentinelStationSummary) {
  const isOp = s.operational && s.temp_c !== null;
  const tempStr = isOp ? `${Math.round(s.temp_c!)}°` : '--';
  const windDir = s.wind_direction_deg || 0;
  const windSpeed = s.wind_speed_kmh ? `${Math.round(s.wind_speed_kmh)}` : '0';

  const badgeColor = s.is_central
    ? 'rgba(14, 165, 233, 0.9)'
    : isOp
    ? 'rgba(30, 41, 59, 0.92)'
    : 'rgba(71, 85, 105, 0.85)';
  const borderColor = s.is_central ? '#38BDF8' : isOp ? '#64748B' : '#475569';

  return L.divIcon({
    className: 'sentinel-radar-marker',
    html: `
      <div style="
        display: inline-flex;
        align-items: center;
        gap: 3px;
        background: ${badgeColor};
        border: 1px solid ${borderColor};
        border-radius: 6px;
        padding: 2px 5px;
        box-shadow: 0 2px 6px rgba(0,0,0,0.6);
        color: #F8FAFC;
        font-family: system-ui, -apple-system, sans-serif;
        font-size: 10px;
        font-weight: 700;
        white-space: nowrap;
        pointer-events: auto;
      ">
        <span style="color: #94A3B8; font-size: 8.5px;">${s.icao}</span>
        <span style="color: #38BDF8;">${tempStr}</span>
        ${
          isOp && s.wind_speed_kmh
            ? `
          <div style="display: inline-flex; align-items: center; gap: 2px; color: #E2E8F0; font-size: 8.5px;">
            <span style="color: #64748B;">·</span>
            <svg style="width: 8px; height: 8px; transform: rotate(${windDir}deg); fill: currentColor;" viewBox="0 0 24 24">
              <path d="M12 2L4.5 20.29l.71.71L12 18l6.79 3 .71-.71z"/>
            </svg>
            <span>${windSpeed}km/h</span>
          </div>`
            : ''
        }
      </div>
    `,
    iconAnchor: [35, 22],
  });
}

export const RadarMap: React.FC<RadarMapProps> = ({ lat, lon, cityName, sentinels }) => {
  const [radarData, setRadarData] = useState<RadarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [activeLayer, setActiveLayer] = useState<LayerMode>('satellite');
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const animationTimerRef = useRef<number | null>(null);

  // Sprint 2.2 Pro Controls
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false);
  const [speedMultiplier, setSpeedMultiplier] = useState<1 | 2>(1);
  const [dataSaver, setDataSaver] = useState<boolean>(() => {
    return localStorage.getItem('climarc_4g_saver') === 'true';
  });
  const [showLightning, setShowLightning] = useState<boolean>(true);
  const [showStations, setShowStations] = useState<boolean>(true);

  // Handle dataSaver toggle with persistence
  const toggleDataSaver = () => {
    setDataSaver((prev) => {
      const next = !prev;
      localStorage.setItem('climarc_4g_saver', String(next));
      return next;
    });
  };

  useEffect(() => {
    let mounted = true;
    async function loadRadar() {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchRadar();
        if (mounted) {
          setRadarData(data);
          const satFrames = data.satellite?.infrared || [];
          if (satFrames.length > 0) {
            setCurrentIndex(satFrames.length - 1); // latest frame
          } else {
            const pastFrames = data.radar?.past || [];
            if (pastFrames.length > 0) {
              setCurrentIndex(pastFrames.length - 1);
            }
          }
        }
      } catch (err: any) {
        if (mounted) {
          setError('No se pudo cargar la telemetría de radar / satélite.');
        }
      } finally {
        if (mounted) setLoading(false);
      }
    }
    loadRadar();
    return () => {
      mounted = false;
    };
  }, []);

  // Compute active frames according to selected layer mode and 4G saver filter
  const allFrames: RadarFrame[] = useMemo(() => {
    if (!radarData) return [];
    let baseFrames: RadarFrame[] = [];
    if (activeLayer === 'radar') {
      const past = radarData.radar?.past || [];
      const nowcast = radarData.radar?.nowcast || [];
      baseFrames = [...past, ...nowcast];
    } else {
      baseFrames = radarData.satellite?.infrared || [];
    }

    if (dataSaver && baseFrames.length > 6) {
      // Take every 2nd frame, always keeping the latest
      return baseFrames.filter((_, i) => i % 2 === (baseFrames.length - 1) % 2);
    }
    return baseFrames;
  }, [activeLayer, radarData, dataSaver]);

  // Handle switching layer modes cleanly
  const handleLayerChange = (mode: LayerMode) => {
    if (mode === activeLayer) return;
    setIsPlaying(false);
    setActiveLayer(mode);
    if (!radarData) return;

    const targetFrames =
      mode === 'radar'
        ? [...(radarData.radar?.past || []), ...(radarData.radar?.nowcast || [])]
        : radarData.satellite?.infrared || [];

    if (targetFrames.length > 0) {
      if (mode === 'radar') {
        const pastCount = radarData.radar?.past?.length || targetFrames.length;
        setCurrentIndex(pastCount - 1);
      } else {
        setCurrentIndex(targetFrames.length - 1);
      }
    } else {
      setCurrentIndex(0);
    }
  };

  // Frame animation timer with speed control (1x = 700ms, 2x = 350ms)
  useEffect(() => {
    if (isPlaying && allFrames.length > 0) {
      const intervalMs = speedMultiplier === 2 ? 350 : 700;
      animationTimerRef.current = window.setInterval(() => {
        setCurrentIndex((prev) => (prev + 1) % allFrames.length);
      }, intervalMs);
    } else {
      if (animationTimerRef.current !== null) {
        clearInterval(animationTimerRef.current);
        animationTimerRef.current = null;
      }
    }

    return () => {
      if (animationTimerRef.current !== null) {
        clearInterval(animationTimerRef.current);
      }
    };
  }, [isPlaying, allFrames.length, speedMultiplier]);

  // Close fullscreen on ESC key
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isFullscreen) {
        setIsFullscreen(false);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isFullscreen]);

  const currentFrame = allFrames[currentIndex];

  // Tile URLs for Esri World Dark Gray Canvas
  const esriDarkBaseUrl =
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}';
  const esriDarkRefUrl =
    'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}';

  const frameTimeStr = currentFrame?.time
    ? new Date(currentFrame.time * 1000).toLocaleTimeString('es-AR', {
        timeZone: 'America/Argentina/Cordoba',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '--:--';

  const isNowcastPrediction =
    activeLayer === 'radar' &&
    radarData?.radar?.past &&
    currentIndex >= radarData.radar.past.length;

  const liveIndex = useMemo(() => {
    if (activeLayer === 'radar' && radarData?.radar?.past?.length) {
      const latestPast = radarData.radar.past[radarData.radar.past.length - 1];
      const foundIdx = allFrames.findIndex((f) => f.time === latestPast.time);
      return foundIdx !== -1 ? foundIdx : Math.min(radarData.radar.past.length - 1, allFrames.length - 1);
    }
    return Math.max(0, allFrames.length - 1);
  }, [activeLayer, radarData, allFrames]);

  const isLatestFrame = currentIndex === liveIndex;

  const hasLightning = Boolean(radarData?.lightning && radarData.lightning.tile_url_template);

  // Render Map Container
  const renderMapContent = (isModal: boolean) => (
    <div className={`relative w-full ${isModal ? 'h-full flex-1' : 'h-64'} rounded-2xl overflow-hidden border border-slate-800 bg-[#0B1120]`}>
      {loading && !radarData && (
        <div className="absolute inset-0 z-[500] flex items-center justify-center bg-slate-900/80">
          <Loader2 className="w-6 h-6 text-sky-400 animate-spin" />
        </div>
      )}
      {error ? (
        <div className="w-full h-full flex flex-col items-center justify-center p-4 text-center text-slate-400 bg-slate-900/80">
          <AlertTriangle className="w-8 h-8 text-amber-400 mb-2" />
          <p className="text-xs">{error}</p>
          <button
            onClick={() => window.location.reload()}
            className="mt-2 text-xs text-sky-400 hover:underline"
          >
            Reintentar
          </button>
        </div>
      ) : (
        <MapContainer
          center={[lat, lon]}
          zoom={isModal ? 8 : activeLayer === 'satellite' ? 7 : 8}
          zoomControl={isModal}
          attributionControl={true}
          scrollWheelZoom={isModal}
          className="w-full h-full"
        >
          <MapViewController
            center={[lat, lon]}
            zoom={isModal ? 8 : activeLayer === 'satellite' ? 7 : 8}
          />

          {/* 1. Esri World Dark Gray Canvas Base Tiles (zIndex 1) */}
          <TileLayer
            url={esriDarkBaseUrl}
            maxNativeZoom={16}
            maxZoom={16}
            zIndex={1}
            attribution='Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
          />

          {/* 2. Zero-flicker Animated Weather Layers (Radar or Infrared Satellite) (zIndex 10) */}
          {allFrames.map((frame, idx) => {
            let frameUrl: string | null = null;
            if (activeLayer === 'radar') {
              frameUrl =
                radarData?.host && frame.path
                  ? `${radarData.host}${frame.path}/256/{z}/{x}/{y}/2/1_1.png`
                  : null;
            } else {
              if (radarData?.satellite?.tile_url_template && frame.time_str) {
                frameUrl = radarData.satellite.tile_url_template.replace('{time}', frame.time_str);
              } else if (radarData?.host && frame.path) {
                frameUrl = `${radarData.host}${frame.path}/256/{z}/{x}/{y}/0/0_0.png`;
              }
            }
            if (!frameUrl) return null;

            const isActive = idx === currentIndex;
            const targetOpacity = activeLayer === 'radar' ? 0.72 : 0.65;

            return (
              <TileLayer
                key={`${activeLayer}-${frame.time_str || frame.path || idx}`}
                url={frameUrl}
                opacity={isActive ? targetOpacity : 0}
                maxNativeZoom={7}
                maxZoom={14}
                keepBuffer={4}
                zIndex={10}
              />
            );
          })}

          {/* 3. GLM Lightning Electrical Activity Layer (zIndex 15) - Only visible on latest live frame */}
          {showLightning && hasLightning && isLatestFrame && radarData?.lightning?.tile_url_template && (
            <TileLayer
              key={`glm-layer-${radarData.lightning.latest_time || 'latest'}`}
              url={radarData.lightning.tile_url_template.replace(
                '{time}',
                radarData.lightning.latest_time || ''
              )}
              opacity={0.88}
              maxNativeZoom={7}
              maxZoom={14}
              keepBuffer={2}
              zIndex={15}
            />
          )}

          {/* 4. Esri Reference Overlay (Cities, borders & roads on top, zIndex 20) */}
          <TileLayer
            url={esriDarkRefUrl}
            maxNativeZoom={16}
            maxZoom={16}
            opacity={0.85}
            zIndex={20}
          />

          {/* Discreet City Center Pin */}
          <Marker position={[lat, lon]} icon={cityMarkerIcon}>
            <Popup className="dark-popup">
              <div className="text-xs font-bold text-slate-900">{cityName}</div>
            </Popup>
          </Marker>

          {/* 5. Peripheral Sentinel Airport Stations Pins */}
          {showStations &&
            sentinels &&
            sentinels.map((s) => (
              <Marker
                key={s.icao}
                position={[s.lat, s.lon]}
                icon={createSentinelIcon(s)}
              >
                <Popup className="dark-popup">
                  <div className="text-xs p-1 space-y-1">
                    <div className="font-extrabold text-slate-900 flex items-center justify-between gap-2">
                      <span>{s.name} ({s.icao})</span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {s.is_central ? 'Estación Central' : `${Math.round(s.distance_km)} km ${s.cardinal}`}
                      </span>
                    </div>
                    {s.operational ? (
                      <div className="text-[11px] text-slate-700 space-y-0.5">
                        <div>
                          Temp: <b>{s.temp_c}°C</b> · Rocío: <b>{s.dewp_c}°C</b>
                        </div>
                        <div>
                          Viento: <b>{Math.round(s.wind_speed_kmh || 0)} km/h</b> (dir {s.wind_direction_deg}°)
                          {s.wind_gust_kmh ? ` · Ráf: ${Math.round(s.wind_gust_kmh)} km/h` : ''}
                        </div>
                        <div>
                          Presión QNH: <b>{s.qnh_hpa ? `${s.qnh_hpa.toFixed(1)} hPa` : '--'}</b>
                        </div>
                        {s.wx_codes && s.wx_codes.length > 0 && (
                          <div className="text-amber-700 font-bold">
                            Fenómenos: {s.wx_codes.join(', ')}
                          </div>
                        )}
                        <div className="text-[9.5px] text-slate-500 pt-0.5 font-mono">
                          {s.raw_metar}
                        </div>
                      </div>
                    ) : (
                      <div className="text-[10px] text-slate-500 italic">
                        Estación fuera de servicio o sin reporte reciente.
                      </div>
                    )}
                  </div>
                </Popup>
              </Marker>
            ))}
        </MapContainer>
      )}

      {/* Time & Prediction Badge overlay on map */}
      {currentFrame && (
        <div className="absolute top-2.5 right-2.5 z-[400] bg-slate-900/85 backdrop-blur-md border border-slate-700/80 text-xs px-2.5 py-1 rounded-lg shadow-md flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full ${
              activeLayer === 'satellite'
                ? 'bg-indigo-400'
                : isNowcastPrediction
                ? 'bg-amber-400'
                : 'bg-emerald-400'
            }`}
          />
          <span className="font-bold text-slate-100">{frameTimeStr}</span>
          {activeLayer === 'radar' && isNowcastPrediction && (
            <span className="text-[10px] text-amber-300 font-semibold">(Pronóstico)</span>
          )}
          {activeLayer === 'satellite' && (
            <span className="text-[10px] text-indigo-300 font-semibold">(GOES-19 IR)</span>
          )}
        </div>
      )}
    </div>
  );

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl overflow-hidden p-4">
        {/* Header with Layer Segmented Control + Fullscreen Action */}
        <div className="flex items-center justify-between mb-3 gap-2">
          <div className="flex items-center gap-2">
            {activeLayer === 'radar' ? (
              <Radio className="w-4 h-4 text-sky-400 animate-pulse shrink-0" />
            ) : (
              <CloudLightning className="w-4 h-4 text-indigo-400 animate-pulse shrink-0" />
            )}
            <h3 className="text-sm font-bold text-slate-100 truncate">
              {activeLayer === 'radar' ? 'Radar en Vivo' : 'Satélite Infrarrojo'}
            </h3>
          </div>

          <div className="flex items-center gap-1.5 shrink-0">
            {/* Segmented Control [ Radar | Satélite IR ] */}
            <div className="flex items-center bg-slate-800/90 p-0.5 rounded-xl border border-slate-700/80">
              <button
                type="button"
                onClick={() => handleLayerChange('radar')}
                className={`px-2 py-1 rounded-lg text-[11px] font-bold transition-all ${
                  activeLayer === 'radar'
                    ? 'bg-sky-500 text-white shadow-sm shadow-sky-500/30'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                Radar
              </button>
              <button
                type="button"
                onClick={() => handleLayerChange('satellite')}
                className={`px-2 py-1 rounded-lg text-[11px] font-bold transition-all ${
                  activeLayer === 'satellite'
                    ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                Satélite
              </button>
            </div>

            {/* Fullscreen Button */}
            <button
              type="button"
              onClick={() => setIsFullscreen(true)}
              title="Ver en Pantalla Completa"
              className="p-1.5 rounded-xl bg-slate-800/80 hover:bg-slate-700 text-slate-300 hover:text-white border border-slate-700/80 transition-all shadow-sm"
              aria-label="Pantalla completa"
            >
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>
        </div>

        {/* Regular Map Box */}
        {renderMapContent(false)}

        {/* Pro Toolbar: Speed + 4G Saver + Lightning GLM + Sentinels */}
        <div className="mt-2.5 flex items-center justify-between text-[11px] bg-slate-900/70 p-2 rounded-xl border border-slate-800/90 gap-1.5">
          {/* Speed Selector (1x / 2x) */}
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-slate-400">Vel:</span>
            <div className="flex rounded-lg bg-slate-800 p-0.5 border border-slate-700 text-[10px]">
              <button
                type="button"
                onClick={() => setSpeedMultiplier(1)}
                className={`px-1.5 py-0.5 rounded font-bold transition-all ${
                  speedMultiplier === 1 ? 'bg-sky-500 text-white' : 'text-slate-400'
                }`}
              >
                1x
              </button>
              <button
                type="button"
                onClick={() => setSpeedMultiplier(2)}
                className={`px-1.5 py-0.5 rounded font-bold transition-all ${
                  speedMultiplier === 2 ? 'bg-sky-500 text-white' : 'text-slate-400'
                }`}
              >
                2x
              </button>
            </div>
          </div>

          {/* 4G Saver Toggle */}
          <button
            type="button"
            onClick={toggleDataSaver}
            title={dataSaver ? 'Ahorro 4G activo (6 cuadros)' : 'Modo completo (12 cuadros)'}
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold transition-all ${
              dataSaver
                ? 'bg-emerald-950/70 border-emerald-700/80 text-emerald-300'
                : 'bg-slate-800/60 border-slate-700 text-slate-400 hover:text-slate-200'
            }`}
          >
            {dataSaver ? <WifiOff className="w-3 h-3 text-emerald-400" /> : <Wifi className="w-3 h-3" />}
            <span>4G Ahorro</span>
          </button>

          {/* Lightning Layer (GLM) Toggle */}
          <button
            type="button"
            onClick={() => setShowLightning(!showLightning)}
            disabled={!hasLightning}
            title="Actividad de rayos en vivo (GOES-East GLM)"
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold transition-all ${
              showLightning && hasLightning
                ? 'bg-amber-950/80 border-amber-500/80 text-amber-300 shadow-sm'
                : 'bg-slate-800/60 border-slate-700 text-slate-400 hover:text-slate-200 opacity-60'
            }`}
          >
            <Zap className={`w-3 h-3 ${showLightning && hasLightning ? 'text-amber-400 animate-bounce' : ''}`} />
            <span>⚡ Rayos</span>
          </button>

          {/* Sentinel Stations Toggle */}
          <button
            type="button"
            onClick={() => setShowStations(!showStations)}
            title="Mostrar estaciones centinela (SAOC, SACO, SAOR, SAOL)"
            className={`flex items-center gap-1 px-2 py-1 rounded-lg border text-[10px] font-bold transition-all ${
              showStations
                ? 'bg-sky-950/70 border-sky-600/80 text-sky-300 shadow-sm'
                : 'bg-slate-800/60 border-slate-700 text-slate-400 hover:text-slate-200'
            }`}
          >
            <Gauge className="w-3 h-3 text-sky-400" />
            <span>Estaciones</span>
          </button>
        </div>

        {/* Animation & Slider Controls Bar */}
        {allFrames.length > 0 && (
          <div className="mt-2.5 flex flex-col gap-2 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className={`p-2 rounded-lg text-white font-bold transition-all active:scale-95 flex items-center justify-center shrink-0 shadow-sm ${
                  activeLayer === 'radar'
                    ? 'bg-sky-500 hover:bg-sky-400 shadow-sky-500/30'
                    : 'bg-indigo-600 hover:bg-indigo-500 shadow-indigo-600/30'
                }`}
                aria-label={isPlaying ? 'Pausar' : 'Reproducir'}
              >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>

              {/* Timeline Slider */}
              <div className="flex-1 flex flex-col gap-1">
                <input
                  type="range"
                  min={0}
                  max={allFrames.length - 1}
                  value={currentIndex}
                  onChange={(e) => {
                    setIsPlaying(false);
                    setCurrentIndex(parseInt(e.target.value, 10));
                  }}
                  className={`w-full h-1.5 bg-slate-700 rounded-lg cursor-pointer ${
                    activeLayer === 'radar' ? 'accent-sky-400' : 'accent-indigo-400'
                  }`}
                />
                <div className="flex justify-between text-[10px] text-slate-400">
                  <span>{activeLayer === 'radar' ? '-2 h' : '-1.5 h'}</span>
                  <span className="text-slate-200 font-semibold">
                    Cuadro {currentIndex + 1} de {allFrames.length}
                  </span>
                  <span>{activeLayer === 'radar' ? '+30 m' : 'Ahora'}</span>
                </div>
              </div>

              <button
                onClick={() => {
                  setIsPlaying(false);
                  if (activeLayer === 'radar') {
                    setCurrentIndex(
                      (radarData?.radar?.past?.length || allFrames.length) - 1
                    );
                  } else {
                    setCurrentIndex(allFrames.length - 1);
                  }
                }}
                title="Volver a la última observación real"
                className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-all shrink-0"
              >
                <RotateCcw className="w-3.5 h-3.5" />
              </button>
            </div>

            {/* Compact Context Legend */}
            <div className="pt-1.5 border-t border-slate-800/80">
              {activeLayer === 'radar' ? (
                <div className="flex items-center justify-between text-[10px] text-slate-400 px-1">
                  <span className="text-slate-300 font-semibold">Reflectividad:</span>
                  <div className="flex items-center gap-1.5 font-medium whitespace-nowrap">
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-cyan-400" />
                      <span>Débil</span>
                    </span>
                    <span className="text-slate-600">·</span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-emerald-400" />
                      <span>Mod.</span>
                    </span>
                    <span className="text-slate-600">·</span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-amber-400" />
                      <span>Fuerte</span>
                    </span>
                    <span className="text-slate-600">·</span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-purple-500" />
                      <span>Granizo</span>
                    </span>
                  </div>
                </div>
              ) : (
                <div className="flex flex-col gap-1 px-1">
                  <div className="flex items-center justify-between text-[10px]">
                    <span className="text-indigo-300 font-semibold flex items-center gap-1">
                      <Radio className="w-3 h-3 text-indigo-400" />
                      GOES-19 Band 13 (IR Gradiente)
                    </span>
                    <span className="text-slate-400 text-[9px]">Topes nubosos</span>
                  </div>
                  {/* Mini color scale for satellite */}
                  <div className="flex items-center justify-between text-[10px] text-slate-400 font-medium whitespace-nowrap">
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-slate-400" />
                      <span>Nubes</span>
                    </span>
                    <span className="text-slate-600">→</span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-emerald-400" />
                      <span>Tormenta</span>
                    </span>
                    <span className="text-slate-600">→</span>
                    <span className="flex items-center gap-1">
                      <span className="w-2 h-2 rounded-full bg-rose-500" />
                      <span className="w-2 h-2 rounded-full bg-purple-500 -ml-0.5" />
                      <span>Severa / Granizo</span>
                    </span>
                  </div>
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* FULLSCREEN MODAL OVERLAY */}
      {isFullscreen && (
        <div className="fixed inset-0 z-[9999] bg-[#0B1120] flex flex-col p-3 sm:p-5 animate-in fade-in duration-200">
          {/* Fullscreen Header */}
          <div className="flex items-center justify-between pb-3 border-b border-slate-800">
            <div className="flex items-center gap-2">
              <span className="text-base font-extrabold text-slate-100">
                {activeLayer === 'radar' ? 'Radar Meteorológico' : 'Satélite GOES-19 IR'}
              </span>
              <span className="text-xs text-slate-400">· Córdoba & Río Cuarto</span>
            </div>

            <div className="flex items-center gap-2">
              {/* Segmented Control */}
              <div className="flex items-center bg-slate-800/90 p-0.5 rounded-xl border border-slate-700/80">
                <button
                  type="button"
                  onClick={() => handleLayerChange('radar')}
                  className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${
                    activeLayer === 'radar' ? 'bg-sky-500 text-white' : 'text-slate-400'
                  }`}
                >
                  Radar
                </button>
                <button
                  type="button"
                  onClick={() => handleLayerChange('satellite')}
                  className={`px-3 py-1 rounded-lg text-xs font-bold transition-all ${
                    activeLayer === 'satellite' ? 'bg-indigo-600 text-white' : 'text-slate-400'
                  }`}
                >
                  Satélite
                </button>
              </div>

              {/* Close Fullscreen */}
              <button
                type="button"
                onClick={() => setIsFullscreen(false)}
                className="flex items-center gap-1.5 px-3 py-1 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-bold text-xs border border-slate-700 transition-all"
              >
                <Minimize2 className="w-4 h-4" />
                <span>Cerrar</span>
              </button>
            </div>
          </div>

          {/* Fullscreen Map Body */}
          <div className="flex-1 w-full my-3">
            {renderMapContent(true)}
          </div>

          {/* Fullscreen Controls Footer */}
          <div className="flex items-center justify-between bg-slate-900/90 p-3 rounded-2xl border border-slate-800">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className={`p-2.5 rounded-xl text-white font-bold transition-all ${
                  activeLayer === 'radar' ? 'bg-sky-500' : 'bg-indigo-600'
                }`}
              >
                {isPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4 ml-0.5" />}
              </button>
              <div className="text-xs text-slate-300 font-bold">
                {frameTimeStr} {isNowcastPrediction ? '(Pronóstico)' : ''}
              </div>
            </div>

            <div className="flex-1 max-w-md mx-4">
              <input
                type="range"
                min={0}
                max={allFrames.length - 1}
                value={currentIndex}
                onChange={(e) => {
                  setIsPlaying(false);
                  setCurrentIndex(parseInt(e.target.value, 10));
                }}
                className="w-full h-2 bg-slate-700 rounded-lg cursor-pointer accent-sky-400"
              />
            </div>

            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setShowLightning(!showLightning)}
                className={`px-2.5 py-1 rounded-lg border text-xs font-bold ${
                  showLightning ? 'bg-amber-950/80 border-amber-500 text-amber-300' : 'bg-slate-800 text-slate-400'
                }`}
              >
                ⚡ Rayos
              </button>
              <button
                type="button"
                onClick={() => setShowStations(!showStations)}
                className={`px-2.5 py-1 rounded-lg border text-xs font-bold ${
                  showStations ? 'bg-sky-950/80 border-sky-500 text-sky-300' : 'bg-slate-800 text-slate-400'
                }`}
              >
                Estaciones
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
};
