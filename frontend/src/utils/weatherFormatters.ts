/**
 * Meteorological formatting utilities for ClimaRC.
 * Localized for Cordoba/Rio Cuarto, Argentina (America/Argentina/Cordoba).
 */

export interface WeatherCondition {
  label: string;
  iconName: 'sun' | 'moon' | 'cloud-sun' | 'cloud-moon' | 'cloud' | 'cloud-rain' | 'cloud-drizzle' | 'cloud-lightning' | 'snowflake' | 'cloud-fog';
}

export function getWeatherCondition(code: number, isDay: boolean = true): WeatherCondition {
  switch (code) {
    case 0:
      return {
        label: isDay ? 'Despejado' : 'Noche despejada',
        iconName: isDay ? 'sun' : 'moon',
      };
    case 1:
      return {
        label: isDay ? 'Mayormente despejado' : 'Mayormente despejado',
        iconName: isDay ? 'cloud-sun' : 'cloud-moon',
      };
    case 2:
      return {
        label: 'Parcialmente nublado',
        iconName: isDay ? 'cloud-sun' : 'cloud-moon',
      };
    case 3:
      return {
        label: 'Nublado',
        iconName: 'cloud',
      };
    case 45:
    case 48:
      return {
        label: 'Niebla / Neblina',
        iconName: 'cloud-fog',
      };
    case 51:
    case 53:
    case 55:
      return {
        label: 'Llovizna',
        iconName: 'cloud-drizzle',
      };
    case 61:
      return {
        label: 'Lluvia débil',
        iconName: 'cloud-rain',
      };
    case 63:
      return {
        label: 'Lluvia moderada',
        iconName: 'cloud-rain',
      };
    case 65:
      return {
        label: 'Lluvia fuerte',
        iconName: 'cloud-rain',
      };
    case 80:
    case 81:
    case 82:
      return {
        label: 'Chaparrones',
        iconName: 'cloud-rain',
      };
    case 71:
    case 73:
    case 75:
      return {
        label: 'Aguanieve / Nieve',
        iconName: 'snowflake',
      };
    case 95:
      return {
        label: 'Tormenta eléctrica',
        iconName: 'cloud-lightning',
      };
    case 96:
      return {
        label: 'Tormenta fuerte',
        iconName: 'cloud-lightning',
      };
    case 99:
      return {
        label: 'Tormenta con granizo',
        iconName: 'cloud-lightning',
      };
    default:
      return {
        label: 'Nubosidad variable',
        iconName: isDay ? 'cloud-sun' : 'cloud-moon',
      };
  }
}

/**
 * Checks if a given UTC ISO timestamp corresponds to daylight in Rio Cuarto / Cordoba.
 * Summer/Spring sunrise ~06:00, sunset ~20:30.
 */
export function isDaylightInArgentina(timestampUtc: string): boolean {
  try {
    const d = new Date(timestampUtc);
    // Convert to Argentina local hour (UTC-3)
    const localHour = (d.getUTCHours() - 3 + 24) % 24;
    return localHour >= 6 && localHour < 21;
  } catch {
    return true;
  }
}

/**
 * Formats a UTC timestamp to Argentine local hour (HH:mm).
 */
export function formatLocalHour(timestampUtc: string): string {
  try {
    const d = new Date(timestampUtc);
    return d.toLocaleTimeString('es-AR', {
      timeZone: 'America/Argentina/Cordoba',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    });
  } catch {
    return '--:--';
  }
}

/**
 * Formats a date string (YYYY-MM-DD or ISO) into friendly Spanish day name (e.g. "Lun 23", "Hoy", "Mañana").
 */
export function formatSpanishDay(dateStr: string, index?: number): string {
  if (index === 0) return 'Hoy';
  if (index === 1) return 'Mañana';
  try {
    // Treat YYYY-MM-DD as local day
    const parts = dateStr.split('-');
    if (parts.length === 3) {
      const d = new Date(parseInt(parts[0]), parseInt(parts[1]) - 1, parseInt(parts[2]), 12, 0, 0);
      const dayName = d.toLocaleDateString('es-AR', { weekday: 'short' });
      const dayNum = d.getDate();
      return `${dayName.charAt(0).toUpperCase() + dayName.slice(1)} ${dayNum}`;
    }
    const d = new Date(dateStr);
    return d.toLocaleDateString('es-AR', { weekday: 'short', day: 'numeric' });
  } catch {
    return dateStr;
  }
}

/**
 * Converts meteorological wind degrees into 16-cardinal compass string.
 */
export function degreesToCardinal(deg: number): string {
  const directions = [
    'N', 'NNE', 'NE', 'ENE',
    'E', 'ESE', 'SE', 'SSE',
    'S', 'SSO', 'SO', 'OSO',
    'O', 'ONO', 'NO', 'NNO'
  ];
  const idx = Math.round(((deg % 360) + 360) % 360 / 22.5) % 16;
  return directions[idx];
}

/**
 * Humanizes age minutes into "hace X min" or "hace X h".
 */
export function formatAgeMinutes(minutes: number | null | undefined): string {
  if (minutes === null || minutes === undefined) return 'desconocido';
  if (minutes < 1) return 'recién';
  if (minutes < 60) return `hace ${Math.round(minutes)} min`;
  const hours = (minutes / 60).toFixed(1);
  return `hace ${hours} h`;
}
