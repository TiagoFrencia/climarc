import React, { useState, useEffect } from 'react';
import {
  X,
  Send,
  Bell,
  Sliders,
  CheckCircle2,
  AlertTriangle,
  Lock,
  Moon,
  Clock,
  Loader2,
  ShieldCheck,
  History,
} from 'lucide-react';
import {
  fetchAlertSettings,
  saveAlertSettings,
  testTelegramAlert,
} from '../services/api';
import type { AlertThresholdSettings, RecentAlertRecord } from '../types/weather';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

function formatAlertTimestamp(utcStr?: string | null): string {
  if (!utcStr) return 'Sin fecha';
  try {
    const d = new Date(utcStr);
    if (isNaN(d.getTime())) return utcStr;
    const parts = new Intl.DateTimeFormat('es-AR', {
      timeZone: 'America/Argentina/Cordoba',
      day: '2-digit',
      month: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(d);

    const day = parts.find((p) => p.type === 'day')?.value || '00';
    const month = parts.find((p) => p.type === 'month')?.value || '00';
    const hour = parts.find((p) => p.type === 'hour')?.value || '00';
    const minute = parts.find((p) => p.type === 'minute')?.value || '00';

    return `${day}/${month} ${hour}:${minute} hs`;
  } catch {
    return utcStr;
  }
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
}) => {
  const [adminPin, setAdminPin] = useState<string>(() => {
    return localStorage.getItem('climarc_admin_pin') || '';
  });

  const [loading, setLoading] = useState<boolean>(false);
  const [saveLoading, setSaveLoading] = useState<boolean>(false);
  const [testLoading, setTestLoading] = useState<boolean>(false);

  const [telegramConfigured, setTelegramConfigured] = useState<boolean>(false);
  const [recentAlerts, setRecentAlerts] = useState<RecentAlertRecord[]>([]);

  // Thresholds state
  const [rainProbPct, setRainProbPct] = useState<number>(60);
  const [rainMm, setRainMm] = useState<number>(1.5);
  const [wrfNearbyMm, setWrfNearbyMm] = useState<number>(8.0);
  const [gustKmh, setGustKmh] = useState<number>(45.0);
  const [tempDropC, setTempDropC] = useState<number>(6.0);
  const [coldApparentC, setColdApparentC] = useState<number>(5.0);
  const [cooldownHours, setCooldownHours] = useState<number>(4);

  // Quiet hours state
  const [quietEnabled, setQuietEnabled] = useState<boolean>(false);
  const [quietStartHour, setQuietStartHour] = useState<number>(23);
  const [quietEndHour, setQuietEndHour] = useState<number>(7);

  // Feedback states
  const [feedback, setFeedback] = useState<{
    type: 'success' | 'error' | 'warning';
    message: string;
  } | null>(null);

  // Save PIN to localStorage on change
  const handlePinChange = (val: string) => {
    setAdminPin(val);
    localStorage.setItem('climarc_admin_pin', val);
  };

  // Fetch settings from API when opened
  useEffect(() => {
    if (!isOpen) return;

    let mounted = true;
    setLoading(true);
    setFeedback(null);

    fetchAlertSettings()
      .then((data) => {
        if (!mounted) return;
        setTelegramConfigured(data.telegram_configured);
        setRecentAlerts(data.recent_alerts || []);

        const s = data.settings;
        if (s) {
          setRainProbPct(s.rain_prob_pct ?? 60);
          setRainMm(s.rain_mm ?? 1.5);
          setWrfNearbyMm(s.wrf_nearby_mm ?? 8.0);
          setGustKmh(s.gust_kmh ?? 45.0);
          setTempDropC(s.temp_drop_c ?? 6.0);
          setColdApparentC(s.cold_apparent_c ?? 5.0);
          setCooldownHours(s.cooldown_hours ?? 4);
          setQuietEnabled(s.quiet_enabled ?? false);
          setQuietStartHour(s.quiet_start_hour ?? 23);
          setQuietEndHour(s.quiet_end_hour ?? 7);
        }
      })
      .catch((err) => {
        if (!mounted) return;
        setFeedback({
          type: 'error',
          message: `Error al cargar ajustes: ${err.message}`,
        });
      })
      .finally(() => {
        if (mounted) setLoading(false);
      });

    return () => {
      mounted = false;
    };
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSaveSettings = async () => {
    setSaveLoading(true);
    setFeedback(null);

    const payload: Partial<AlertThresholdSettings> = {
      rain_prob_pct: Number(rainProbPct),
      rain_mm: Number(rainMm),
      wrf_nearby_mm: Number(wrfNearbyMm),
      gust_kmh: Number(gustKmh),
      temp_drop_c: Number(tempDropC),
      cold_apparent_c: Number(coldApparentC),
      cooldown_hours: Number(cooldownHours),
      quiet_enabled: quietEnabled,
      quiet_start_hour: Number(quietStartHour),
      quiet_end_hour: Number(quietEndHour),
    };

    try {
      const res = await saveAlertSettings(payload, adminPin);
      setFeedback({
        type: 'success',
        message: res.message || 'Ajustes guardados correctamente en la base de datos.',
      });
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('401')) {
        setFeedback({
          type: 'error',
          message: 'Error 401: PIN de administrador incorrecto o no configurado.',
        });
      } else {
        setFeedback({
          type: 'error',
          message: `No se pudieron guardar los ajustes: ${msg}`,
        });
      }
    } finally {
      setSaveLoading(false);
    }
  };

  const handleTestAlert = async () => {
    setTestLoading(true);
    setFeedback(null);

    try {
      const res = await testTelegramAlert(adminPin);
      if (res.result?.sent) {
        setFeedback({
          type: 'success',
          message: '¡Alerta de prueba enviada con éxito a tu chat de Telegram!',
        });
      } else if (res.result?.reason === 'telegram_not_configured') {
        setFeedback({
          type: 'warning',
          message: 'Telegram no está configurado. Definí TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID en el backend (.env).',
        });
      } else {
        setFeedback({
          type: 'error',
          message: `Fallo al enviar a Telegram: ${res.result?.reason || 'Error desconocido'}.`,
        });
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('401')) {
        setFeedback({
          type: 'error',
          message: 'Error 401: PIN de administrador incorrecto para ejecutar pruebas.',
        });
      } else {
        setFeedback({
          type: 'error',
          message: `Error de conexión: ${msg}`,
        });
      }
    } finally {
      setTestLoading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-3 sm:p-4 bg-black/80 backdrop-blur-sm animate-fadeIn">
      <div className="relative w-full max-w-lg bg-[#0e1626] border border-slate-800 rounded-3xl shadow-2xl p-5 overflow-hidden max-h-[92vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-800/80">
          <div className="flex items-center gap-2.5">
            <div className="p-2 rounded-xl bg-sky-950/80 text-sky-400 border border-sky-800/60">
              <Sliders className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100 flex items-center gap-1.5">
                Ajustes y Motor de Alertas
              </h2>
              <p className="text-[11px] text-slate-400">
                Notificaciones automáticas proactivas vía Telegram
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
        <div className="overflow-y-auto py-3.5 space-y-4 pr-1 text-xs text-slate-300">
          {/* Feedback banner */}
          {feedback && (
            <div
              className={`p-3 rounded-xl border flex items-start gap-2 animate-fadeIn ${
                feedback.type === 'success'
                  ? 'bg-emerald-950/50 border-emerald-800/70 text-emerald-300'
                  : feedback.type === 'warning'
                  ? 'bg-amber-950/50 border-amber-800/70 text-amber-300'
                  : 'bg-rose-950/50 border-rose-800/70 text-rose-300'
              }`}
            >
              {feedback.type === 'success' ? (
                <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" />
              ) : (
                <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              )}
              <div className="text-[11px] leading-relaxed font-medium">
                {feedback.message}
              </div>
            </div>
          )}

          {/* Telegram Status & Security Badge */}
          <div className="p-3.5 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-bold text-slate-200">
                <Send className="w-4 h-4 text-sky-400" />
                <span>Canal de Notificaciones Telegram</span>
              </div>
              <span
                className={`px-2.5 py-0.5 rounded-full text-[10px] font-semibold border flex items-center gap-1 ${
                  telegramConfigured
                    ? 'bg-emerald-950/80 border-emerald-800 text-emerald-400'
                    : 'bg-amber-950/80 border-amber-800 text-amber-400'
                }`}
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full ${
                    telegramConfigured ? 'bg-emerald-400 animate-pulse' : 'bg-amber-400'
                  }`}
                />
                {telegramConfigured ? 'Conectado' : 'Pendiente en .env'}
              </span>
            </div>

            <p className="text-[11px] text-slate-400 leading-relaxed">
              {telegramConfigured
                ? 'Las credenciales del bot están configuradas de forma segura en las variables de entorno del servidor. El frontend no almacena tokens sensibles.'
                : 'Por seguridad, las credenciales del bot se configuran exclusivamente en el backend (.env). No se solicitan tokens en la web pública.'}
            </p>

            {/* Admin PIN input */}
            <div className="pt-1">
              <div className="flex items-center justify-between mb-1">
                <label className="text-[10px] uppercase font-bold text-slate-400 flex items-center gap-1">
                  <Lock className="w-3 h-3 text-sky-400" />
                  PIN de Administrador (X-ADMIN-PIN)
                </label>
                <span className="text-[9px] text-slate-500">Guardado en este dispositivo</span>
              </div>
              <input
                type="password"
                placeholder="Ingresá tu PIN para guardar cambios o probar"
                value={adminPin}
                onChange={(e) => handlePinChange(e.target.value)}
                className="w-full bg-slate-950/90 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 placeholder:text-slate-600 focus:outline-none focus:border-sky-500 font-mono tracking-wider"
              />
            </div>

            {/* Test alert button */}
            <button
              onClick={handleTestAlert}
              disabled={testLoading || loading}
              className="w-full py-2 px-3 rounded-xl bg-slate-800/90 hover:bg-slate-700/90 border border-slate-700/60 text-slate-200 font-semibold transition-all active:scale-[0.98] flex items-center justify-center gap-2 disabled:opacity-50"
            >
              {testLoading ? (
                <>
                  <Loader2 className="w-3.5 h-3.5 animate-spin text-sky-400" />
                  <span>Enviando alerta de prueba...</span>
                </>
              ) : (
                <>
                  <Send className="w-3.5 h-3.5 text-sky-400" />
                  <span>Enviar alerta de prueba a Telegram</span>
                </>
              )}
            </button>
          </div>

          {/* Trigger Thresholds */}
          <div className="p-3.5 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-3">
            <div className="flex items-center gap-2 font-bold text-slate-200">
              <Bell className="w-4 h-4 text-amber-400" />
              <span>Umbrales de Activación Automática</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
              {/* Rain Prob */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Probabilidad de lluvia inminente (%)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0"
                    max="100"
                    value={rainProbPct}
                    onChange={(e) => setRainProbPct(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">%</span>
                </div>
              </div>

              {/* Rain MM */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Precipitación prevista en 2h (mm)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    value={rainMm}
                    onChange={(e) => setRainMm(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">mm</span>
                </div>
              </div>

              {/* WRF 3x3 Nearby Core */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Núcleo WRF circundante 3x3 (mm/h)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.5"
                    min="0"
                    value={wrfNearbyMm}
                    onChange={(e) => setWrfNearbyMm(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">mm/h</span>
                </div>
              </div>

              {/* Wind Gusts */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Ráfagas de viento fuertes (km/h)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    min="0"
                    value={gustKmh}
                    onChange={(e) => setGustKmh(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">km/h</span>
                </div>
              </div>

              {/* Temp drop */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Caída térmica brusca en ≤3h (°C)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="0.5"
                    min="1"
                    value={tempDropC}
                    onChange={(e) => setTempDropC(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">°C</span>
                </div>
              </div>

              {/* Cold apparent */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80">
                <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                  Sensación térmica mínima crítica (°C)
                </label>
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    step="1"
                    value={coldApparentC}
                    onChange={(e) => setColdApparentC(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-sky-500"
                  />
                  <span className="text-[11px] text-slate-500 font-mono">°C</span>
                </div>
              </div>

              {/* Cooldown Hours */}
              <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800/80 sm:col-span-2">
                <div className="flex items-center justify-between mb-1">
                  <label className="text-[10px] font-semibold text-slate-400 flex items-center gap-1">
                    <Clock className="w-3 h-3 text-sky-400" />
                    Cooldown Anti-Spam (horas entre alertas idénticas)
                  </label>
                  <span className="text-[10px] font-mono text-sky-400 font-bold">
                    {cooldownHours} hs
                  </span>
                </div>
                <input
                  type="range"
                  min="1"
                  max="12"
                  step="0.5"
                  value={cooldownHours}
                  onChange={(e) => setCooldownHours(Number(e.target.value))}
                  className="w-full accent-sky-500 cursor-pointer"
                />
                <p className="text-[9px] text-slate-500 mt-1">
                  Si un fenómeno escala de nivel (ej. amarillo a naranja/rojo), la alerta se envía de inmediato eludiendo el cooldown.
                </p>
              </div>
            </div>
          </div>

          {/* Quiet Hours */}
          <div className="p-3.5 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-bold text-slate-200">
                <Moon className="w-4 h-4 text-indigo-400" />
                <span>Horario de Silencio (Hora Córdoba)</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={quietEnabled}
                  onChange={(e) => setQuietEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
              </label>
            </div>

            <p className="text-[11px] text-slate-400 leading-relaxed">
              Durante este rango horario solo se silencian las alertas preventivas (lluvia común, ráfagas moderadas).
              <span className="text-amber-300/90 font-medium ml-1">
                Las alertas críticas de Prioridad 1 (granizo, tormentas activas, ráfagas extremas) suenan siempre.
              </span>
            </p>

            {quietEnabled && (
              <div className="grid grid-cols-2 gap-3 pt-1 animate-fadeIn">
                <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                  <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                    Inicio de silencio
                  </label>
                  <select
                    value={quietStartHour}
                    onChange={(e) => setQuietStartHour(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                  >
                    {Array.from({ length: 24 }).map((_, i) => (
                      <option key={i} value={i}>
                        {i.toString().padStart(2, '0')}:00 hs
                      </option>
                    ))}
                  </select>
                </div>

                <div className="p-2.5 rounded-xl bg-slate-950/60 border border-slate-800">
                  <label className="text-[10px] font-semibold text-slate-400 block mb-1">
                    Fin de silencio
                  </label>
                  <select
                    value={quietEndHour}
                    onChange={(e) => setQuietEndHour(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-100 font-mono focus:outline-none focus:border-indigo-500"
                  >
                    {Array.from({ length: 24 }).map((_, i) => (
                      <option key={i} value={i}>
                        {i.toString().padStart(2, '0')}:00 hs
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* Recent Alerts Audit */}
          <div className="p-3.5 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-slate-200">
              <History className="w-4 h-4 text-sky-400" />
              <span>Últimas Alertas Registradas / Enviadas</span>
            </div>

            {recentAlerts.length === 0 ? (
              <p className="text-[11px] text-slate-500 italic py-1">
                No hay alertas registradas recientemente en la base de datos.
              </p>
            ) : (
              <div className="space-y-1.5 pt-1">
                {recentAlerts.slice(0, 5).map((a, idx) => (
                  <div
                    key={idx}
                    className="p-2 rounded-xl bg-slate-950/60 border border-slate-800/80 flex items-start justify-between gap-2 text-[10px]"
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-semibold text-slate-200 line-clamp-2 leading-snug">
                        {a.payload_summary || a.alert_key}
                      </div>
                      <div className="text-slate-500 text-[9px] font-mono mt-0.5">
                        {formatAlertTimestamp(a.last_sent_utc)}
                      </div>
                    </div>
                    <span
                      className={`px-2 py-0.5 rounded text-[9px] font-bold uppercase shrink-0 ${
                        a.severity === 'rojo'
                          ? 'bg-rose-950 text-rose-300 border border-rose-800'
                          : a.severity === 'naranja'
                          ? 'bg-amber-950 text-amber-300 border border-amber-800'
                          : 'bg-yellow-950 text-yellow-300 border border-yellow-800'
                      }`}
                    >
                      {a.severity}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-slate-800/80 flex items-center justify-between gap-3">
          <button
            onClick={onClose}
            className="px-4 py-2 bg-slate-800/80 hover:bg-slate-700 text-slate-300 font-semibold rounded-xl text-xs transition-colors"
          >
            Cerrar
          </button>

          <button
            onClick={handleSaveSettings}
            disabled={saveLoading || loading}
            className="flex-1 py-2 px-4 bg-sky-600 hover:bg-sky-500 active:scale-[0.98] text-white font-bold rounded-xl text-xs transition-all shadow-md shadow-sky-600/20 flex items-center justify-center gap-1.5 disabled:opacity-50"
          >
            {saveLoading ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
                <span>Guardando...</span>
              </>
            ) : (
              <>
                <ShieldCheck className="w-4 h-4" />
                <span>Guardar Ajustes</span>
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
};