import React, { useState, useEffect, useRef, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import { Play, Pause, RotateCcw, AlertTriangle, Radio, Loader2, CloudLightning } from 'lucide-react';
import type { RadarResponse, RadarFrame } from '../types/weather';
import { fetchRadar } from '../services/api';

interface RadarMapProps {
  lat: number;
  lon: number;
  cityName: string;
}

type LayerMode = 'radar' | 'satellite';

// Controller to smoothly pan map on city switch
function MapViewController({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, zoom, { animate: true });
  }, [center, zoom, map]);
  return null;
}

// Sleek discreet pin (subtle ring with translucent center so text remains readable)
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

export const RadarMap: React.FC<RadarMapProps> = ({ lat, lon, cityName }) => {
  const [radarData, setRadarData] = useState<RadarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [activeLayer, setActiveLayer] = useState<LayerMode>('satellite');
  const [currentIndex, setCurrentIndex] = useState<number>(0);
  const [isPlaying, setIsPlaying] = useState<boolean>(false);
  const animationTimerRef = useRef<number | null>(null);

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
            setCurrentIndex(satFrames.length - 1); // Set to latest satellite frame
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

  // Compute active frames according to selected layer mode
  const allFrames: RadarFrame[] = useMemo(() => {
    if (!radarData) return [];
    if (activeLayer === 'radar') {
      const past = radarData.radar?.past || [];
      const nowcast = radarData.radar?.nowcast || [];
      return [...past, ...nowcast];
    } else {
      return radarData.satellite?.infrared || [];
    }
  }, [activeLayer, radarData]);

  // Handle switching layer modes cleanly without index out of bounds
  const handleLayerChange = (mode: LayerMode) => {
    if (mode === activeLayer) return;
    setIsPlaying(false);
    setActiveLayer(mode);
    if (!radarData) return;

    const targetFrames =
      mode === 'radar'
        ? [...(radarData.radar?.past || []), ...(radarData.radar?.nowcast || [])]
        : (radarData.satellite?.infrared || []);

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

  // Frame animation timer
  useEffect(() => {
    if (isPlaying && allFrames.length > 0) {
      animationTimerRef.current = window.setInterval(() => {
        setCurrentIndex((prev) => (prev + 1) % allFrames.length);
      }, 700);
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
  }, [isPlaying, allFrames.length]);

  const currentFrame = allFrames[currentIndex];

  // Tile URLs for Esri World Dark Gray Canvas (100% free, no API key required)
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

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl overflow-hidden p-4">
        {/* Header with Layer Segmented Control */}
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

          {/* Segmented Control [ Radar | Satélite IR ] */}
          <div className="flex items-center bg-slate-800/90 p-0.5 rounded-xl border border-slate-700/80 shrink-0">
            <button
              type="button"
              onClick={() => handleLayerChange('radar')}
              className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
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
              className={`px-2.5 py-1 rounded-lg text-[11px] font-bold transition-all ${
                activeLayer === 'satellite'
                  ? 'bg-indigo-600 text-white shadow-sm shadow-indigo-600/30'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              Satélite IR
            </button>
          </div>
        </div>

        {/* Map Box */}
        <div className="relative w-full h-64 rounded-2xl overflow-hidden border border-slate-800 bg-[#0B1120]">
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
              zoom={activeLayer === 'satellite' ? 7 : 8}
              zoomControl={false}
              attributionControl={true}
              scrollWheelZoom={false}
              className="w-full h-full"
            >
              <MapViewController
                center={[lat, lon]}
                zoom={activeLayer === 'satellite' ? 7 : 8}
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

              {/* 3. Esri Reference Overlay (Cities, borders & roads on top, zIndex 20) */}
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

        {/* Animation & Slider Controls Bar */}
        {allFrames.length > 0 && (
          <div className="mt-3 flex flex-col gap-2 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800">
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
    </section>
  );
};
