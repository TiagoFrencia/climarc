/**
 * Type definitions matching 100% of ClimaRC FastAPI Backend JSON responses.
 * Audited against /api/weather, /api/radar, /api/accuracy and /api/health.
 */

export interface CityConfig {
  key: string;
  name: string;
  lat: number;
  lon: number;
  icao: string;
}

export interface CurrentWeather {
  timestamp_utc: string;
  temperature_c: number;
  apparent_temperature_c: number;
  relative_humidity_pct: number | null;
  wind_speed_kmh: number;
  wind_gust_kmh: number;
  wind_direction_deg: number;
  pressure_hpa: number | null;
  surface_pressure_hpa: number | null;
  weather_code: number;
  nowcast_blended: boolean;
  confidence: 'alta' | 'media' | 'baja';
  raw_ensemble_temp_c: number;
  ml_temp_adjustment_c: number;
  ml_wind_adjustment_kmh: number;
  ml_applied: boolean;
  metar_station: string | null;
  metar_age_minutes: number | null;
  metar_raw: string;
}

export interface ModelStepData {
  temperature_c?: number | null;
  precip_mm?: number | null;
  wind_speed_kmh?: number | null;
  precip_nearby_max_mm?: number | null;
}

export interface HourlyForecast {
  lead_hour: number;
  timestamp_utc: string;
  temperature_c: number;
  apparent_temperature_c: number;
  relative_humidity_pct: number | null;
  precip_mm: number;
  precip_prob_pct: number;
  wind_speed_kmh: number;
  wind_gust_kmh: number;
  wind_direction_deg: number;
  pressure_hpa: number | null;
  surface_pressure_hpa: number | null;
  weather_code: number;
  cape_jkg: number;
  confidence: 'alta' | 'media' | 'baja';
  temp_std_dev: number;
  nowcast_blended: boolean;
  raw_ensemble_temp_c: number;
  raw_ensemble_wind_kmh: number;
  ml_temp_adjustment_c: number;
  ml_wind_adjustment_kmh: number;
  ml_applied: boolean;
  wrf_nearby_precip_max_mm: number | null;
  models_comparison: {
    ecmwf?: ModelStepData;
    icon?: ModelStepData;
    gfs?: ModelStepData;
    wrf?: ModelStepData;
  };
}

export interface DailyForecast {
  date: string;
  temperature_max_c: number;
  temperature_min_c: number;
  precipitation_sum_mm: number;
  precipitation_probability_max_pct: number;
  wind_speed_max_kmh: number;
  weather_code: number;
}

export interface WeatherAlert {
  id?: string | number;
  title: string;
  description: string;
  level: 'amarillo' | 'naranja' | 'rojo' | string;
  type?: string;
  source: string;
  zones?: string[];
}

export interface MLMetricsDetails {
  mae_before?: number;
  mae_after?: number;
  rmse_before?: number;
  rmse_after?: number;
  day_mae_before?: number;
  day_mae_after?: number;
  night_mae_before?: number;
  night_mae_after?: number;
  improvement_pct?: number;
}

export interface MLMetadata {
  available: boolean;
  status?: string;
  model_version?: string;
  station?: string;
  city?: string;
  trained_at_utc?: string;
  samples_matched?: number;
  temp_improved?: boolean;
  wind_improved?: boolean;
  temperature_metrics?: MLMetricsDetails;
  wind_metrics?: MLMetricsDetails;
  valid_utc_hours_count?: number;
}

export interface WeatherPayload {
  city: CityConfig;
  updated_at_utc: string;
  current: CurrentWeather;
  hourly: HourlyForecast[];
  daily: DailyForecast[];
  alerts: WeatherAlert[];
  models_active: string[];
  confidence: 'alta' | 'media' | 'baja';
  wrf_available: boolean;
  smn_official_available: boolean;
  ml_metadata: MLMetadata;
}

export interface RadarFrame {
  time: number;
  path: string;
}

export interface RadarResponse {
  version: string;
  generated: number;
  host: string;
  radar: {
    past: RadarFrame[];
    nowcast?: RadarFrame[];
  };
  satellite?: {
    infrared?: RadarFrame[];
  };
}

export interface AccuracyResponse {
  city: string;
  icao: string;
  model_source: string;
  samples_count: number;
  mae_temperature_c: number | null;
  mae_wind_kmh: number | null;
  status: string;
  message?: string;
}

export interface HealthSourceWrf {
  available: boolean;
  run_time_utc: string | null;
  age_hours: number | null;
  reason?: string | null;
}

export interface HealthSourceMetar {
  saoc_records_cached: number;
  saco_records_cached: number;
  latest_saoc_utc: string | null;
  latest_saco_utc: string | null;
}

export interface HealthSourceSmn {
  jwt_configured: boolean;
  status: string;
}

export interface HealthSourceDatabase {
  status: string;
  path: string;
}

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  timestamp_utc: string;
  sources: {
    wrf: HealthSourceWrf;
    metar: HealthSourceMetar;
    smn_official: HealthSourceSmn;
    database: HealthSourceDatabase;
  };
}

export interface AlertThresholdSettings {
  rain_prob_pct: number;
  rain_mm: number;
  wrf_nearby_mm: number;
  gust_kmh: number;
  temp_drop_c: number;
  cold_apparent_c: number;
  cape_jkg: number;
  cooldown_hours: number;
  quiet_enabled: boolean;
  quiet_start_hour: number;
  quiet_end_hour: number;
  updated_at_utc?: string;
}

export interface RecentAlertRecord {
  city: string;
  alert_key: string;
  severity: string;
  last_sent_utc: string;
  payload_summary: string;
}

export interface AlertSettingsResponse {
  settings: AlertThresholdSettings;
  telegram_configured: boolean;
  recent_alerts: RecentAlertRecord[];
}

export interface AlertTestResponse {
  status: 'ok' | 'warning' | string;
  result: {
    sent: boolean;
    reason?: string;
    detail?: string;
    message_id?: number;
  };
}
