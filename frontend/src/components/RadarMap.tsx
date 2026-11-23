import React, { useState, useEffect, useRef, useMemo } from 'react';
import { MapContainer, TileLayer, Marker, Popup, useMap } from 'react-leaflet';
import L from 'leaflet';
import { Play, Pause, RotateCcw, AlertTriangle, Radio, Loader2 } from 'lucide-react';
import type { RadarResponse, RadarFrame } from '../types/weather';
import { fetchRadar } from '../services/api';

interface RadarMapProps {
  lat: number;
  lon: number;
  cityName: string;
}

// Controller to smoothly pan map on city switch
function MapViewController({ center, zoom }: { center: [number, number]; zoom: number }) {
  const map = useMap();
  useEffect(() => {
    map.setView(center, zoom, { animate: true });
  }, [center, zoom, map]);
  return null;
}

// Sleek dark-mode glowing pin
const cityMarkerIcon = L.divIcon({
  className: 'custom-radar-pin',
  html: `
    <div style="position: relative; width: 20px; height: 20px; display: flex; align-items: center; justify-content: center;">
      <div style="position: absolute; width: 20px; height: 20px; background: rgba(56, 189, 248, 0.35); border-radius: 50%; animation: ping 1.8s cubic-bezier(0, 0, 0.2, 1) infinite;"></div>
      <div style="width: 12px; height: 12px; background: #38BDF8; border: 2.5px solid #0B1120; border-radius: 50%; box-shadow: 0 0 8px #38BDF8;"></div>
    </div>
  `,
  iconSize: [20, 20],
  iconAnchor: [10, 10],
});

export const RadarMap: React.FC<RadarMapProps> = ({ lat, lon, cityName }) => {
  const [radarData, setRadarData] = useState<RadarResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
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
          const pastFrames = data.radar?.past || [];
          if (pastFrames.length > 0) {
            setCurrentIndex(pastFrames.length - 1); // Set to latest frame
          }
        }
      } catch (err: any) {
        if (mounted) {
          setError('No se pudo cargar la reflectividad de radar (RainViewer).');
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

  const allFrames: RadarFrame[] = useMemo(() => {
    if (!radarData?.radar) return [];
    const past = radarData.radar.past || [];
    const nowcast = radarData.radar.nowcast || [];
    return [...past, ...nowcast];
  }, [radarData]);

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

  // Tile URL for current RainViewer radar frame
  const radarTileUrl =
    radarData?.host && currentFrame?.path
      ? `${radarData.host}${currentFrame.path}/256/{z}/{x}/{y}/2/1_1.png`
      : null;

  const frameTimeStr = currentFrame?.time
    ? new Date(currentFrame.time * 1000).toLocaleTimeString('es-AR', {
        timeZone: 'America/Argentina/Cordoba',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '--:--';

  const isNowcastPrediction =
    radarData?.radar?.past && currentIndex >= radarData.radar.past.length;

  return (
    <section className="px-4 py-2 max-w-md mx-auto w-full">
      <div className="rounded-3xl bg-[#111827] border border-slate-800 shadow-xl overflow-hidden p-4">
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Radio className="w-4 h-4 text-sky-400 animate-pulse" />
            <h3 className="text-sm font-bold text-slate-100">
              Radar Meteorológico en Vivo
            </h3>
          </div>
          <span className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider bg-slate-800 px-2 py-0.5 rounded-full border border-slate-700">
            RainViewer Nowcasting
          </span>
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
              zoom={8}
              zoomControl={false}
              attributionControl={true}
              scrollWheelZoom={false}
              className="w-full h-full"
            >
              <MapViewController center={[lat, lon]} zoom={8} />

              {/* Esri World Dark Gray Canvas Base Tiles (Free, no API key) */}
              <TileLayer
                url={esriDarkBaseUrl}
                maxNativeZoom={16}
                maxZoom={16}
                attribution='Tiles &copy; Esri &mdash; Esri, DeLorme, NAVTEQ'
              />

              {/* Esri Reference Overlay (Cities, borders & roads) */}
              <TileLayer
                url={esriDarkRefUrl}
                maxNativeZoom={16}
                maxZoom={16}
                opacity={0.85}
                zIndex={5}
              />

              {/* Animated RainViewer Radar Layer (maxNativeZoom=7 to prevent Zoom Level Not Supported) */}
              {radarTileUrl && (
                <TileLayer
                  key={currentFrame?.path || 'radar-layer'}
                  url={radarTileUrl}
                  opacity={0.72}
                  maxNativeZoom={7}
                  maxZoom={14}
                  zIndex={10}
                />
              )}

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
                  isNowcastPrediction ? 'bg-amber-400' : 'bg-emerald-400'
                }`}
              />
              <span className="font-bold text-slate-100">{frameTimeStr}</span>
              {isNowcastPrediction && (
                <span className="text-[10px] text-amber-300 font-semibold">(Pronóstico)</span>
              )}
            </div>
          )}
        </div>

        {/* Radar Controls Bar */}
        {allFrames.length > 0 && (
          <div className="mt-3 flex flex-col gap-2 bg-slate-900/60 p-2.5 rounded-xl border border-slate-800">
            <div className="flex items-center gap-3">
              <button
                onClick={() => setIsPlaying(!isPlaying)}
                className="p-2 rounded-lg bg-sky-500 hover:bg-sky-400 text-white font-bold transition-all active:scale-95 flex items-center justify-center shrink-0 shadow-sm shadow-sky-500/30"
                aria-label={isPlaying ? 'Pausar radar' : 'Reproducir radar'}
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
                  className="w-full accent-sky-400 h-1.5 bg-slate-700 rounded-lg cursor-pointer"
                />
                <div className="flex justify-between text-[10px] text-slate-400">
                  <span>-2 h</span>
                  <span className="text-slate-200 font-semibold">
                    Cuadro {currentIndex + 1} de {allFrames.length}
                  </span>
                  <span>+30 m</span>
                </div>
              </div>

              <button
                onClick={() => {
                  setIsPlaying(false);
                  setCurrentIndex(
                    (radarData?.radar?.past?.length || allFrames.length) - 1
                  );
                }}
                title="Volver a la última observación real"
                className="p-2 rounded-lg text-slate-400 hover:text-white hover:bg-slate-800 transition-all shrink-0"
              >
                <RotateCcw className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        )}
      </div>
    </section>
  );
};
