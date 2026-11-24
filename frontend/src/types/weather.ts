/**
/**
 * Type definitions matching 100% of ClimaRC FastAPI Backend JSON responses.
 * Audited against /api/weather, /api/radar, /api/route-weather, /api/accuracy and /api/health.
 */

export interface CityConfig {
  key: string;
  name: string;
  lat: number;
  lon: number;
  icao: string;
}

export type QnhTrend = 'subiendo' | 'estable' | 'bajando' | 'bajando_rapido' | null;

export interface CurrentWeather {
  timestamp_utc: string;
  temperature_c: number;
  apparent_temperature_c: number;
  dew_point_c?: number | null;
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
  metar_wx_raw?: string | null;
  is_precipitating_now?: boolean;

  // Sprint 2.1 & 2.2 enriched metrics
  qnh_trend?: QnhTrend;
  qnh_delta_3h_hpa?: number | null;
  uv_index?: number | null;
  sunrise_local?: string | null;
  sunset_local?: string | null;
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
  uv_index?: number | null;
  models_comparison: {
    ecmwf?: ModelStepData;
    icon?: ModelStepData;
    gfs?: ModelStepData;
    wrf?: ModelStepData;
    aifs?: ModelStepData;
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
  sunrise_local?: string | null;
  sunset_local?: string | null;
  uv_index_max?: number | null;
}

export interface WeatherAlert {
  id?: string | number;
  title: string;
  description: string;
  level: 'amarillo' | 'naranja' | 'rojo' | string;
  type?: string;
  source: string;
  zones?: string[];
  metadata?: Record<string, any>;
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

export interface Stage2Details {
  available: boolean;
  severe_core_detected: boolean;
  extreme_core_detected?: boolean;
  severe_pixels_count?: number;
  extreme_pixels_count?: number;
  severe_pixels_pct?: number;
  min_brightness_temp_c?: number | null;
  timestamp_str?: string | null;
}

export interface HailRiskEvaluation {
  level: 'sin_riesgo' | 'moderado' | 'alto' | 'extremo';
  score: number;
  peak_window_local: string;
  cape_max: number;
  shear_0_6km_kmh: number;
  freezing_level_m: number;
  goes_severe_core_detected: boolean;
  sentinel_hail_detected: boolean;
  imminent_hail_alert: boolean;
  cirrus_rejected: boolean;
  explanation: string;
  stage2_details?: Stage2Details;
}

export interface SentinelStationSummary {
  icao: string;
  name: string;
  description: string;
  lat: number;
  lon: number;
  is_central: boolean;
  distance_km: number;
  bearing_deg: number;
  cardinal: string;
  operational: boolean;
  temp_c: number | null;
  dewp_c: number | null;
  humidity_pct: number | null;
  wind_speed_kmh: number | null;
  wind_gust_kmh: number | null;
  wind_direction_deg: number | null;
  qnh_hpa: number | null;
  wx_codes: string[];
  raw_metar: string;
  obs_time_utc: string | null;
  age_minutes: number | null;
}

export interface SentinelFrontDetection {
  detected: boolean;
  station_icao?: string;
  station_name?: string;
  distance_km?: number;
  cardinal?: string;
  estimated_arrival_minutes?: number;
  triggers?: string[];
  headline?: string;
  urgency?: 'inmediata' | 'proxima' | 'monitoreo';
  reason?: string;
}

export interface WeatherPayload {
  city: CityConfig;
  updated_at_utc: string;
  summary_one_liner?: string;
  current: CurrentWeather;
  hourly: HourlyForecast[];
  daily: DailyForecast[];
  sentinels?: SentinelStationSummary[];
  sentinel_front?: SentinelFrontDetection;
  hail_risk?: HailRiskEvaluation;
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
  time_str?: string;
}

export interface SatelliteMetadata {
  provider?: string;
  product?: string;
  name?: string;
  tile_url_template?: string;
  max_native_zoom?: number;
  infrared?: RadarFrame[];
}

export interface LightningMetadata {
  provider: string;
  product: string;
  name: string;
  tile_url_template: string;
  max_native_zoom: number;
  latest_time?: string | null;
  frames: RadarFrame[];
}

export interface RadarResponse {
  version: string;
  generated: number;
  host: string;
  radar: {
    past: RadarFrame[];
    nowcast?: RadarFrame[];
  };
  satellite?: SatelliteMetadata;
  lightning?: LightningMetadata;
}

export interface ModelAccuracyLeaderboardItem {
  model_source: string;
  display_name: string;
  mae_temperature_c: number | null;
  mae_wind_kmh: number | null;
  samples_count: number;
}

export interface AccuracyResponse {
  city: string;
  icao: string;
  days?: number;
  model_source: string;
  samples_count: number;
  mae_temperature_c: number | null;
  mae_wind_kmh: number | null;
  status: string;
  message?: string;
  leaderboard?: ModelAccuracyLeaderboardItem[];
  adaptive_weights_active?: boolean;
  min_samples_required?: number;
  current_weights?: Record<string, number>;
  progress_samples?: {
    samples_accumulated: number;
    target: number;
    ready: boolean;
  };
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
  path?: string;
  engine?: string;
  persistent?: boolean;
  target?: string;
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
  briefing_enabled?: boolean;
  briefing_send_hour?: number;
  briefing_window_start?: number;
  briefing_window_end?: number;
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

// Sprint 2.2 Route Weather types
export interface RouteSegment {
  segment_id: 'origin' | 'midpoint' | 'destination' | string;
  name: string;
  lat: number;
  lon: number;
  status: 'optimo' | 'precaucion' | 'peligroso';
  temperature_c: number;
  apparent_temperature_c: number;
  condition: string;
  weather_code: number;
  rain_mm: number;
  precip_prob_pct: number;
  wind_speed_kmh: number;
  wind_gust_kmh: number;
  wind_direction_deg: number;
  visibility_km?: number | null;
  summary: string;
}

export interface RouteWeatherResponse {
  origin: string;
  origin_name: string;
  destination: string;
  destination_name: string;
  route_name: string;
  global_status: 'optimo' | 'precaucion' | 'peligroso';
  max_rain_mm: number;
  max_gust_kmh: number;
  summary: string;
  updated_at_utc: string;
  segments: RouteSegment[];
}
