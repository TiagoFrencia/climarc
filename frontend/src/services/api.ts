import type {
  WeatherPayload,
  RadarResponse,
  AccuracyResponse,
  HealthResponse,
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

export async function fetchAccuracy(city: string = 'rio_cuarto'): Promise<AccuracyResponse> {
  const targetUrl = API_BASE_URL
    ? `${API_BASE_URL}/api/accuracy?city=${encodeURIComponent(city)}`
    : `/api/accuracy?city=${encodeURIComponent(city)}`;
  const res = await fetch(targetUrl);
  return handleResponse<AccuracyResponse>(res);
}

export async function fetchHealth(): Promise<HealthResponse> {
  const targetUrl = API_BASE_URL ? `${API_BASE_URL}/api/health` : '/api/health';
  const res = await fetch(targetUrl);
  return handleResponse<HealthResponse>(res);
}
