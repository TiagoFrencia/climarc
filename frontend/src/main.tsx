import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { registerSW } from 'virtual:pwa-register';
import './index.css';
import App from './App.tsx';

// Auto-register PWA service worker with NetworkFirst live cache strategy
registerSW({
  immediate: true,
  onNeedRefresh() {
    console.log('[ClimaRC PWA] Nueva versión disponible');
  },
  onOfflineReady() {
    console.log('[ClimaRC PWA] Lista para funcionar sin conexión');
  },
});

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
