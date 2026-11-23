"""
Generates high-res icons for ClimaRC PWA (SVG + PNG 192x192, 512x512, maskables).
"""
import os
import math
from PIL import Image, ImageDraw

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "public")
os.makedirs(OUTPUT_DIR, exist_ok=True)

SVG_CONTENT = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512">
  <defs>
    <linearGradient id="bgGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#0F172A" />
      <stop offset="100%" stop-color="#020617" />
    </linearGradient>
    <linearGradient id="cloudGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#38BDF8" />
      <stop offset="100%" stop-color="#0284C7" />
    </linearGradient>
    <linearGradient id="boltGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FDE047" />
      <stop offset="100%" stop-color="#EAB308" />
    </linearGradient>
    <filter id="glow" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="8" result="blur" />
      <feComposite in="SourceGraphic" in2="blur" operator="over" />
    </filter>
  </defs>

  <!-- Background Squircle -->
  <rect x="16" y="16" width="480" height="480" rx="108" fill="url(#bgGrad)" stroke="#1E293B" stroke-width="4" />

  <!-- Radar Rings behind cloud -->
  <circle cx="256" cy="230" r="140" fill="none" stroke="#38BDF8" stroke-opacity="0.15" stroke-width="4" stroke-dasharray="12 12" />
  <circle cx="256" cy="230" r="90" fill="none" stroke="#0284C7" stroke-opacity="0.25" stroke-width="3" />

  <!-- Main Weather Cloud -->
  <g filter="url(#glow)">
    <path d="M190 280 C155 280 130 255 130 220 C130 190 152 165 182 161 C196 125 230 100 270 100 C318 100 358 135 366 182 C395 188 416 214 416 244 C416 278 388 306 354 306 L190 306 Z"
          fill="url(#cloudGrad)" />
  </g>

  <!-- High-contrast Lightning Bolt / Radar Pulse -->
  <polygon points="266,270 230,340 276,340 240,420 310,326 272,326" fill="url(#boltGrad)" />

  <!-- Raindrops -->
  <ellipse cx="170" cy="350" rx="7" ry="16" transform="rotate(20 170 350)" fill="#38BDF8" opacity="0.85" />
  <ellipse cx="350" cy="355" rx="7" ry="16" transform="rotate(20 350 355)" fill="#38BDF8" opacity="0.85" />

  <!-- Text Badge -->
  <text x="256" y="468" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, sans-serif" font-weight="800" font-size="34" fill="#94A3B8" letter-spacing="3">CLIMARC</text>
</svg>'''


def draw_icon(size: int, maskable: bool = False) -> Image.Image:
    # 3x supersampling for high anti-aliasing quality
    scale = 3
    s = size * scale
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)

    bg_color = (11, 17, 32, 255) # #0B1120

    if maskable:
        # Fill full square for maskable Android adaptive icon
        draw.rectangle([0, 0, s, s], fill=bg_color)
    else:
        # Rounded rectangle
        corner_r = int(s * 0.22)
        draw.rounded_rectangle([int(s * 0.03), int(s * 0.03), int(s * 0.97), int(s * 0.97)], radius=corner_r, fill=bg_color, outline=(30, 41, 59, 255), width=max(1, int(4 * (s / 512))))

    # Draw radar concentric circles
    center_x = s // 2
    center_y = int(s * 0.45)
    r1 = int(s * 0.28)
    r2 = int(s * 0.18)

    draw.ellipse([center_x - r1, center_y - r1, center_x + r1, center_y + r1], outline=(56, 189, 248, 50), width=max(1, int(3 * (s / 512))))
    draw.ellipse([center_x - r2, center_y - r2, center_x + r2, center_y + r2], outline=(2, 132, 199, 80), width=max(1, int(3 * (s / 512))))

    # Draw Stylized Cloud
    # Base pill + circular lobes
    cloud_color = (56, 189, 248, 255) # #38BDF8 cyan-sky
    cloud_dark = (2, 132, 199, 255)   # #0284C7 ocean

    # Lobes
    c1 = (int(s * 0.38), int(s * 0.44), int(s * 0.12)) # Left lobe
    c2 = (int(s * 0.52), int(s * 0.36), int(s * 0.16)) # Center top
    c3 = (int(s * 0.68), int(s * 0.44), int(s * 0.11)) # Right lobe

    # Bottom bar
    bx0, by0, bx1, by1 = int(s * 0.30), int(s * 0.45), int(s * 0.74), int(s * 0.56)

    draw.rounded_rectangle([bx0, by0, bx1, by1], radius=int(s * 0.05), fill=cloud_dark)
    draw.ellipse([c1[0]-c1[2], c1[1]-c1[2], c1[0]+c1[2], c1[1]+c1[2]], fill=cloud_color)
    draw.ellipse([c2[0]-c2[2], c2[1]-c2[2], c2[0]+c2[2], c2[1]+c2[2]], fill=cloud_color)
    draw.ellipse([c3[0]-c3[2], c3[1]-c3[2], c3[0]+c3[2], c3[1]+c3[2]], fill=cloud_dark)

    # Lightning Bolt
    bolt_pts = [
        (int(s * 0.52), int(s * 0.50)),
        (int(s * 0.45), int(s * 0.63)),
        (int(s * 0.53), int(s * 0.63)),
        (int(s * 0.46), int(s * 0.79)),
        (int(s * 0.61), int(s * 0.61)),
        (int(s * 0.53), int(s * 0.61)),
    ]
    draw.polygon(bolt_pts, fill=(250, 204, 21, 255)) # yellow #FACC15

    # Raindrops
    drop_col = (56, 189, 248, 200)
    for dx, dy in [(int(s * 0.36), int(s * 0.66)), (int(s * 0.68), int(s * 0.67))]:
        draw.ellipse([dx - int(s*0.015), dy - int(s*0.035), dx + int(s*0.015), dy + int(s*0.035)], fill=drop_col)

    # Downscale with Lanczos for crystal sharpness
    res = im.resize((size, size), Image.Resampling.LANCZOS)
    return res


def main():
    # Save SVG
    svg_path = os.path.join(OUTPUT_DIR, "favicon.svg")
    with open(svg_path, "w", encoding="utf-8") as f:
        f.write(SVG_CONTENT)
    print(f"Wrote {svg_path}")

    # Save PNGs
    targets = [
        ("pwa-192x192.png", 192, False),
        ("pwa-512x512.png", 512, False),
        ("pwa-maskable-192x192.png", 192, True),
        ("pwa-maskable-512x512.png", 512, True),
        ("apple-touch-icon.png", 180, False),
    ]

    for filename, sz, mask in targets:
        p = os.path.join(OUTPUT_DIR, filename)
        img = draw_icon(sz, maskable=mask)
        img.save(p, "PNG")
        print(f"Saved {p} ({sz}x{sz}, maskable={mask})")


if __name__ == "__main__":
    main()
