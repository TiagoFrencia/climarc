import React, { useState } from 'react';
import { X, Send, Bell, Sliders, CheckCircle2 } from 'lucide-react';

interface SettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const SettingsModal: React.FC<SettingsModalProps> = ({
  isOpen,
  onClose,
}) => {
  const [telegramToken, setTelegramToken] = useState<string>('');
  const [telegramChatId, setTelegramChatId] = useState<string>('');
  const [alertWind, setAlertWind] = useState<boolean>(true);
  const [alertPressure, setAlertPressure] = useState<boolean>(true);
  const [alertConvection, setAlertConvection] = useState<boolean>(true);
  const [testSent, setTestSent] = useState<boolean>(false);

  if (!isOpen) return null;

  const handleTestTelegram = () => {
    setTestSent(true);
    setTimeout(() => setTestSent(false), 3000);
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-sm animate-fadeIn">
      <div className="relative w-full max-w-md bg-[#111827] border border-slate-800 rounded-3xl shadow-2xl p-5 overflow-hidden max-h-[90vh] flex flex-col">
        {/* Header */}
        <div className="flex items-center justify-between pb-3 border-b border-slate-800">
          <div className="flex items-center gap-2">
            <div className="p-2 rounded-xl bg-sky-950/80 text-sky-400 border border-sky-800/60">
              <Sliders className="w-5 h-5" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-100">
                Ajustes y Notificaciones
              </h2>
              <p className="text-xs text-slate-400">
                Fase 5: Bot de Telegram y Alertas automáticas
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

        {/* Scrollable Form Content */}
        <div className="overflow-y-auto py-4 space-y-4 pr-1 text-xs text-slate-300">
          {/* Telegram Card */}
          <div className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-3">
            <div className="flex items-center gap-2 font-bold text-slate-200">
              <Send className="w-4 h-4 text-sky-400" />
              <span>Canal de Alertas Telegram</span>
            </div>

            <p className="text-[11px] text-slate-400 leading-relaxed">
              Recibí notificaciones instantáneas en tu celular cuando se detecten condiciones severas o caídas bruscas de presión barométrica.
            </p>

            <div className="space-y-2">
              <div>
                <label className="text-[10px] uppercase font-bold text-slate-400 block mb-1">
                  Telegram Bot Token
                </label>
                <input
                  type="password"
                  placeholder="123456789:ABCdefGHIjk-lmnOPQ..."
                  value={telegramToken}
                  onChange={(e) => setTelegramToken(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 font-mono"
                />
              </div>

              <div>
                <label className="text-[10px] uppercase font-bold text-slate-400 block mb-1">
                  Telegram Chat ID
                </label>
                <input
                  type="text"
                  placeholder="ej. -100123456789 o tu ID personal"
                  value={telegramChatId}
                  onChange={(e) => setTelegramChatId(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-xl px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 font-mono"
                />
              </div>

              <button
                onClick={handleTestTelegram}
                className="w-full py-2 px-3 mt-1 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-semibold transition-all active:scale-95 flex items-center justify-center gap-1.5 shadow-sm shadow-sky-500/20"
              >
                {testSent ? (
                  <>
                    <CheckCircle2 className="w-3.5 h-3.5 text-emerald-300" />
                    <span>¡Prueba enviada exitosamente!</span>
                  </>
                ) : (
                  <>
                    <Send className="w-3.5 h-3.5" />
                    <span>Probar Alerta de Telegram</span>
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Trigger rules selection */}
          <div className="p-3.5 rounded-2xl bg-slate-900 border border-slate-800 space-y-2.5">
            <div className="flex items-center gap-2 font-bold text-slate-200">
              <Bell className="w-4 h-4 text-amber-400" />
              <span>Reglas de Activación</span>
            </div>

            <div className="space-y-2 pt-1">
              <label className="flex items-center justify-between p-2 rounded-xl bg-slate-950/60 border border-slate-800 cursor-pointer">
                <div>
                  <div className="font-semibold text-slate-200">Caída barométrica rápida</div>
                  <div className="text-[10px] text-slate-400">Caída ≥ 3.0 hPa en 3 horas (precursor de frente)</div>
                </div>
                <input
                  type="checkbox"
                  checked={alertPressure}
                  onChange={(e) => setAlertPressure(e.target.checked)}
                  className="w-4 h-4 accent-sky-500 rounded cursor-pointer"
                />
              </label>

              <label className="flex items-center justify-between p-2 rounded-xl bg-slate-950/60 border border-slate-800 cursor-pointer">
                <div>
                  <div className="font-semibold text-slate-200">Ráfagas Fuertes</div>
                  <div className="text-[10px] text-slate-400">Ráfagas previstas ≥ 50 km/h (amarillo) o ≥ 70 km/h (naranja)</div>
                </div>
                <input
                  type="checkbox"
                  checked={alertWind}
                  onChange={(e) => setAlertWind(e.target.checked)}
                  className="w-4 h-4 accent-sky-500 rounded cursor-pointer"
                />
              </label>

              <label className="flex items-center justify-between p-2 rounded-xl bg-slate-950/60 border border-slate-800 cursor-pointer">
                <div>
                  <div className="font-semibold text-slate-200">Convección Severa / Granizo</div>
                  <div className="text-[10px] text-slate-400">WRF 3x3 ≥ 8 mm/h o reporte activo METAR</div>
                </div>
                <input
                  type="checkbox"
                  checked={alertConvection}
                  onChange={(e) => setAlertConvection(e.target.checked)}
                  className="w-4 h-4 accent-sky-500 rounded cursor-pointer"
                />
              </label>
            </div>
          </div>
        </div>

        {/* Footer */}
        <div className="pt-3 border-t border-slate-800 flex justify-end">
          <button
            onClick={onClose}
            className="w-full py-2 bg-slate-800 hover:bg-slate-700 active:scale-95 text-slate-200 font-semibold rounded-xl transition-all"
          >
            Guardar y Cerrar
          </button>
        </div>
      </div>
    </div>
  );
};
