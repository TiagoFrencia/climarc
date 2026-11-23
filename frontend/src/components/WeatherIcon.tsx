import React from 'react';
import {
  Sun,
  Moon,
  CloudSun,
  CloudMoon,
  Cloud,
  CloudRain,
  CloudDrizzle,
  CloudLightning,
  Snowflake,
  CloudFog,
} from 'lucide-react';
import { getWeatherCondition } from '../utils/weatherFormatters';

interface WeatherIconProps {
  code: number;
  isDay?: boolean;
  className?: string;
}

export const WeatherIcon: React.FC<WeatherIconProps> = ({
  code,
  isDay = true,
  className = 'w-6 h-6',
}) => {
  const cond = getWeatherCondition(code, isDay);

  switch (cond.iconName) {
    case 'sun':
      return <Sun className={`${className} text-amber-400`} strokeWidth={2} />;
    case 'moon':
      return <Moon className={`${className} text-indigo-300`} strokeWidth={2} />;
    case 'cloud-sun':
      return <CloudSun className={`${className} text-amber-300`} strokeWidth={2} />;
    case 'cloud-moon':
      return <CloudMoon className={`${className} text-indigo-300`} strokeWidth={2} />;
    case 'cloud':
      return <Cloud className={`${className} text-slate-300`} strokeWidth={2} />;
    case 'cloud-rain':
      return <CloudRain className={`${className} text-sky-400`} strokeWidth={2} />;
    case 'cloud-drizzle':
      return <CloudDrizzle className={`${className} text-sky-300`} strokeWidth={2} />;
    case 'cloud-lightning':
      return <CloudLightning className={`${className} text-amber-400`} strokeWidth={2.2} />;
    case 'snowflake':
      return <Snowflake className={`${className} text-cyan-200`} strokeWidth={2} />;
    case 'cloud-fog':
      return <CloudFog className={`${className} text-slate-400`} strokeWidth={2} />;
    default:
      return <Sun className={`${className} text-amber-400`} strokeWidth={2} />;
  }
};
