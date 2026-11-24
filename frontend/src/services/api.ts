import type {
  WeatherPayload,
  RadarResponse,
  AccuracyResponse,
  HealthResponse,
  AlertSettingsResponse,
  AlertThresholdSettings,
  AlertTestResponse,
  RouteWeatherResponse,
} from '../types/weather';

const API_BASE_URL = (import.meta.env.VITE_API_URL || '').replace(/\/$/, '');

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const errorBody = await res.text().catch(() => '');
    throw new Error(`Error HTTP ${res.status}: ${errorBody || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export async function fetchWeather(params: {
  city?: string;
  lat?: number;
  lon?: number;
  refresh?: boolean;
}): Promise<WeatherPayload> {
  const url = new URL(`${API_BASE_URL}/api/weather`, window.location.origin);
  if (params.city) {
    url.searchParams.set('city', params.city);
  }
  if (params.lat !== undefined && params.lon !== undefined) {
    url.searchParams.set('lat', params.lat.toString());
    url.searchParams.set('lon', params.lon.toString());
  }
  if (params.refresh) {
    url.searchParams.set('refresh', 'true');
  }

  // If API_BASE_URL is relative or absolute
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/weather?${url.searchParams.toString()}` : `/api/weather?${url.searchParams.toString()}`;

  const res = await fetch(targetUrl);
  return handleResponse<WeatherPayload>(res);
}

export async function fetchRadar(): Promise<RadarResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/radar` : '/api/radar';
  const res = await fetch(targetUrl);
  return handleResponse<RadarResponse>(res);
}

export async function fetchAccuracy(city: string = 'rio_cuarto', days: number = 7): Promise<AccuracyResponse> {
  const targetUrl = API_BASE_URL
    ? `${API_BASE_URL}/api/accuracy?city=${encodeURIComponent(city)}&days=${days}`
    : `/api/accuracy?city=${encodeURIComponent(city)}&days=${days}`;
  const res = await fetch(targetUrl);
  return handleResponse<AccuracyResponse>(res);
}

export async function fetchHealth(): Promise<HealthResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/health` : '/api/health';
  const res = await fetch(targetUrl);
  return handleResponse<HealthResponse>(res);
}

export async function fetchAlertSettings(): Promise<AlertSettingsResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/alerts/settings` : '/api/alerts/settings';
  const res = await fetch(targetUrl);
  return handleResponse<AlertSettingsResponse>(res);
}

export async function saveAlertSettings(
  settings: Partial<AlertThresholdSettings>,
  adminPin?: string
): Promise<{ status: string; settings: AlertThresholdSettings; message?: string }> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/alerts/settings` : '/api/alerts/settings';
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (adminPin && adminPin.trim()) {
    headers['X-ADMIN-PIN'] = adminPin.trim();
  }

  const res = await fetch(targetUrl, {
    method: 'PUT',
    headers,
    body: JSON.stringify(settings),
  });
  return handleResponse<{ status: string; settings: AlertThresholdSettings; message?: string }>(res);
}

export async function testTelegramAlert(adminPin?: string): Promise<AlertTestResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/alerts/test` : '/api/alerts/test';
  const headers: Record<string, string> = {};
  if (adminPin && adminPin.trim()) {
    headers['X-ADMIN-PIN'] = adminPin.trim();
  }

  const res = await fetch(targetUrl, {
    method: 'POST',
    headers,
  });
  return handleResponse<AlertTestResponse>(res);
}

export async function testTelegramBriefing(adminPin?: string): Promise<AlertTestResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/alerts/briefing/test` : '/api/alerts/briefing/test';
  const headers: Record<string, string> = {};
  if (adminPin && adminPin.trim()) {
    headers['X-ADMIN-PIN'] = adminPin.trim();
  }

  const res = await fetch(targetUrl, {
    method: 'POST',
    headers,
  });
  return handleResponse<AlertTestResponse>(res);
}

export async function fetchRouteWeather(params: {
  origin?: string;
  destination: string;
  destLat?: number;
  destLon?: number;
}): Promise<RouteWeatherResponse> {
  const url = new URL(`${API_BASE_URL}/api/route-weather`, window.location.origin);
  if (params.origin) url.searchParams.set('origin', params.origin);
  if (params.destination) url.searchParams.set('destination', params.destination);
  if (params.destLat !== undefined && params.destLon !== undefined) {
    url.searchParams.set('dest_lat', params.destLat.toString());
    url.searchParams.set('dest_lon', params.destLon.toString());
  }
  const targetUrl = API_BASE_URL
    ? `${API_BASE_URL}/api/route-weather?${url.searchParams.toString()}`
    : `/api/route-weather?${url.searchParams.toString()}`;
  const res = await fetch(targetUrl);
  return handleResponse<RouteWeatherResponse>(res);
}
