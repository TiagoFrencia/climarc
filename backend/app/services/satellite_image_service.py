"""
ClimaRC v2.3 - Satellite Image Service for Telegram
Downloads a 2x2 grid of zoom 6 tiles covering Córdoba / San Luis / La Pampa
of GOES-19 Band 13 (Clean IR enhanced GRAD) layered over Esri Dark Gray Base
and Esri Dark Gray Reference.

Composes a clean ~512x512 px PNG in memory (BytesIO, zero disk writes),
draws sharp markers for Río Cuarto (SAOC) and Villa Reynolds (SAOR),
and adds a bottom telemetry bar with local time and minimum cloud-top temperature.
"""

import asyncio
import io
import math
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import httpx
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..config import settings
from .hail_service import scan_goes19_band13

logger = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
    CORDOBA_TZ = ZoneInfo("America/Argentina/Cordoba")
except Exception:
    CORDOBA_TZ = timezone(timedelta(hours=-3))

REALEARTH_TIMES_URL = "https://realearth.ssec.wisc.edu/api/times?products=G19-ABI-FD-BAND13-GRAD"
REALEARTH_TILE_TEMPLATE = "https://realearth.ssec.wisc.edu/tiles/G19-ABI-FD-BAND13-GRAD/6/{x}/{y}.png"
ESRI_BASE_TEMPLATE = "https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/6/{y}/{x}"
ESRI_REF_TEMPLATE = "https://services.arcgisonline.com/arcgis/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/6/{y}/{x}"

# Zoom 6 grid covering Cordoba, San Luis, and northern La Pampa
GRID_X = [19, 20]
GRID_Y = [37, 38]
TILE_SIZE = 256
CANVAS_SIZE = (512, 512)

# Strategic ground stations to mark
STATIONS = [
    {
        "icao": "SAOC",
        "name": "Río Cuarto",
        "lat": -33.1232,
        "lon": -64.3493,
        "color": (239, 68, 68),  # Coral Red
    },
    {
        "icao": "SAOR",
        "name": "Villa Reynolds",
        "lat": -33.728,
        "lon": -65.381,
        "color": (245, 158, 11),  # Amber
    },
]


def latlon_to_pixel(lat: float, lon: float, z: int = 6) -> Tuple[int, int]:
    """Converts latitude and longitude into canvas pixel coordinates for GRID_X and GRID_Y at zoom 6."""
    n = 2 ** z
    x = (lon + 180.0) / 360.0 * n
    lat_rad = math.radians(lat)
    y = (1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * n

    px = int(round((x - GRID_X[0]) * TILE_SIZE))
    py = int(round((y - GRID_Y[0]) * TILE_SIZE))
    return px, py


async def fetch_latest_satellite_time(client: httpx.AsyncClient) -> Optional[str]:
    """Fetches the latest available timestamp for GOES-19 GRAD."""
    headers = {"User-Agent": settings.USER_AGENT}
    try:
        resp = await client.get(REALEARTH_TIMES_URL, headers=headers, timeout=4.0)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                times = data.get("G19-ABI-FD-BAND13-GRAD", [])
            elif isinstance(data, list):
                times = data
            else:
                times = []
            if times:
                return times[-1]
    except Exception as exc:
        logger.warning(f"Error consultando tiempos satelitales en RealEarth: {exc}")
    return None


async def fetch_single_tile(
    url: str,
    client: httpx.AsyncClient,
    headers: Dict[str, str],
) -> Optional[bytes]:
    """Fetches a single PNG tile over HTTP."""
    try:
        resp = await client.get(url, headers=headers, timeout=6.0)
        if resp.status_code == 200:
            return resp.content
    except Exception:
        pass
    return None


def get_convective_status(min_temp_c: float) -> Tuple[str, str]:
    """Returns convective classification label and icon based on cloud-top temperature."""
    if min_temp_c <= -70.0:
        return "Tope extremo (< -70°C)", "⚠️"
    elif min_temp_c <= -60.0:
        return "Convección severa (< -60°C)", "⚡"
    elif min_temp_c <= -45.0:
        return "Nubosidad fría convectiva", "🌧️"
    elif min_temp_c <= -20.0:
        return "Nubosidad media / alta", "☁️"
    else:
        return "Cielo mayormente despejado", "☀️"


def draw_station_marker(
    draw: ImageDraw.ImageDraw,
    px: int,
    py: int,
    label: str,
    color: Tuple[int, int, int],
    font: ImageFont.ImageFont,
):
    """Draws a clean, high-visibility station marker with label pill."""
    r = 5
    # Outer dark shadow
    draw.ellipse([px - r - 1, py - r - 1, px + r + 1, py + r + 1], fill=(15, 23, 42, 220))
    # Filled color circle
    draw.ellipse([px - r, py - r, px + r, py + r], fill=color, outline=(255, 255, 255, 255), width=2)
    # Center pinpoint
    draw.point((px, py), fill=(255, 255, 255, 255))

    # Text label box offset to the right
    text_x = px + 9
    text_y = py - 8

    # Calculate text bounding box
    bbox = font.getbbox(label)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    # Rounded background pill for label
    pad_x = 4
    pad_y = 2
    box_rect = [
        text_x - pad_x,
        text_y - pad_y,
        text_x + text_w + pad_x,
        text_y + text_h + pad_y + 1,
    ]
    draw.rounded_rectangle(box_rect, radius=3, fill=(15, 23, 42, 210), outline=(203, 213, 225, 180), width=1)
    draw.text((text_x, text_y), label, fill=(255, 255, 255, 255), font=font)


async def generate_goes19_telegram_image(
    client: Optional[httpx.AsyncClient] = None,
) -> Tuple[bytes, float, str, str]:
    """
    Downloads tiles and composes a 512x512 annotated GOES-19 satellite image in memory.
    Returns:
        (image_bytes, min_temp_c, timestamp_str, convective_status)
    """
    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=10.0)
        close_client = True

    try:
        latest_time = await fetch_latest_satellite_time(client)
        headers = {"User-Agent": settings.USER_AGENT}

        # Build download tasks for Base, Sat, and Reference tiles concurrently
        base_tasks = []
        sat_tasks = []
        ref_tasks = []

        coords = [(x, y) for y in GRID_Y for x in GRID_X]

        for x, y in coords:
            base_url = ESRI_BASE_TEMPLATE.format(x=x, y=y)
            base_tasks.append(fetch_single_tile(base_url, client, headers))

            sat_url = REALEARTH_TILE_TEMPLATE.format(x=x, y=y)
            if latest_time:
                sat_url += f"?time={latest_time}"
            sat_tasks.append(fetch_single_tile(sat_url, client, headers))

            ref_url = ESRI_REF_TEMPLATE.format(x=x, y=y)
            ref_tasks.append(fetch_single_tile(ref_url, client, headers))

        all_results = await asyncio.gather(*(base_tasks + sat_tasks + ref_tasks), return_exceptions=True)

        n = len(coords)
        base_tiles = all_results[:n]
        sat_tiles = all_results[n : 2 * n]
        ref_tiles = all_results[2 * n :]

        # Determine if Esri base & ref tiles are fully available
        esri_available = (
            all(isinstance(t, bytes) and len(t) > 100 for t in base_tiles) and
            all(isinstance(t, bytes) and len(t) > 100 for t in ref_tiles)
        )

        # Create canvas layers
        base_canvas = Image.new("RGBA", CANVAS_SIZE, (24, 28, 36, 255))
        sat_canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
        ref_canvas = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))

        # Paste tiles in order
        for idx, (x, y) in enumerate(coords):
            col = x - GRID_X[0]
            row = y - GRID_Y[0]
            offset = (col * TILE_SIZE, row * TILE_SIZE)

            # 1. Base Tile
            if esri_available and isinstance(base_tiles[idx], bytes):
                try:
                    b_img = Image.open(io.BytesIO(base_tiles[idx])).convert("RGBA")
                    base_canvas.paste(b_img, offset)
                except Exception as e:
                    logger.debug(f"Error decodificando tile base {x},{y}: {e}")

            # 2. Satellite Tile
            if isinstance(sat_tiles[idx], bytes):
                try:
                    raw_s_img = Image.open(io.BytesIO(sat_tiles[idx]))
                    s_rgba = raw_s_img.convert("RGBA")
                    # If base map is available, apply ~85% opacity so underlying roads/shores show subtly
                    if esri_available:
                        r, g, b, a = s_rgba.split()
                        a = a.point(lambda p: int(p * 0.85))
                        s_rgba.putalpha(a)
                    sat_canvas.paste(s_rgba, offset)
                except Exception as e:
                    logger.debug(f"Error decodificando tile sat {x},{y}: {e}")

            # 3. Reference Tile
            if esri_available and isinstance(ref_tiles[idx], bytes):
                try:
                    r_img = Image.open(io.BytesIO(ref_tiles[idx])).convert("RGBA")
                    ref_canvas.paste(r_img, offset)
                except Exception as e:
                    logger.debug(f"Error decodificando tile ref {x},{y}: {e}")

        # Composite layers
        if esri_available:
            composite = Image.alpha_composite(base_canvas, sat_canvas)
            composite = Image.alpha_composite(composite, ref_canvas)
        else:
            logger.info("Fallback Esri activo: usando únicamente tiles GOES-19 con fondo oscuro.")
            composite = Image.new("RGBA", CANVAS_SIZE, (20, 24, 33, 255))
            composite.paste(sat_canvas, (0, 0), sat_canvas)

        # Minimum cloud top brightness temperature strictly from calibrated grayscale GOES-19 Band 13 scanner
        sat_scan = await scan_goes19_band13(client=client)
        min_temp_c = sat_scan.get("min_brightness_temp_c") if sat_scan else None
        if min_temp_c is None and sat_scan:
            min_temp_c = sat_scan.get("center_brightness_temp_c")
        if min_temp_c is None:
            min_temp_c = 15.0  # Clear sky default when no cloud tops detected
        else:
            min_temp_c = round(float(min_temp_c), 1)

        convective_label, convective_icon = get_convective_status(min_temp_c)

        # Setup drawing and font
        draw = ImageDraw.Draw(composite)
        try:
            font_station = ImageFont.load_default(size=11)
            font_bar_bold = ImageFont.load_default(size=12)
            font_bar_sub = ImageFont.load_default(size=11)
        except Exception:
            font_station = ImageFont.load_default()
            font_bar_bold = ImageFont.load_default()
            font_bar_sub = ImageFont.load_default()

        # Draw station markers
        for st in STATIONS:
            px, py = latlon_to_pixel(st["lat"], st["lon"])
            if 0 <= px < CANVAS_SIZE[0] and 0 <= py < CANVAS_SIZE[1]:
                label = f"{st['icao']} ({st['name']})"
                draw_station_marker(draw, px, py, label, st["color"], font_station)

        # Draw Bottom Telemetry Bar
        bar_height = 44
        bar_top = CANVAS_SIZE[1] - bar_height
        # Dark navy banner background
        draw.rectangle([0, bar_top, CANVAS_SIZE[0], CANVAS_SIZE[1]], fill=(15, 23, 42, 245))
        # Top cyan divider line
        draw.line([0, bar_top, CANVAS_SIZE[0], bar_top], fill=(56, 189, 248, 200), width=1)

        # Local time in Cordoba
        now_cordoba = datetime.now(CORDOBA_TZ)
        time_str = now_cordoba.strftime("%d/%m %H:%M")

        title_text = "🛰️ GOES-19 Ch13 IR · Córdoba y Región"
        telemetry_text = f"{time_str} hs  |  Tope mín: {min_temp_c:.1f} °C  {convective_icon}"

        draw.text((12, bar_top + 6), title_text, fill=(241, 245, 249, 255), font=font_bar_bold)
        draw.text((12, bar_top + 23), telemetry_text, fill=(148, 163, 184, 255), font=font_bar_sub)

        # Status badge on right side of bottom bar
        draw.text((CANVAS_SIZE[0] - 180, bar_top + 14), convective_label, fill=(254, 240, 138, 255), font=font_bar_sub)

        # Export clean PNG to BytesIO (zero disk writing)
        output_bio = io.BytesIO()
        composite.convert("RGB").save(output_bio, format="PNG", optimize=True)
        output_bio.seek(0)

        image_bytes = output_bio.getvalue()
        logger.info(
            f"Imagen satelital GOES-19 generada en memoria: {len(image_bytes)} bytes, "
            f"tope mín: {min_temp_c}°C ({convective_label}), timestamp: {latest_time or time_str}"
        )

        return image_bytes, min_temp_c, latest_time or time_str, convective_label

    finally:
        if close_client:
            await client.aclose()
