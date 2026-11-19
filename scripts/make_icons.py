"""Regenerate every website icon from the 1024×1024 app icon (full-bleed purple square with the white A).

    uv run python scripts/make_icons.py [path/to/1024.png]

Default source: brand/app-icon-1024.png. Writes to public/ (served at the site root by WhiteNoise, where browsers
look for /favicon.ico and /apple-touch-icon.png) and console/static/console/icons/ (the console's web app manifest).
- favicon.ico (16, 32, 48, 64, 128, 256) and icon-32/icon-96 PNGs: rounded corners on a transparent background,
  so tabs (Safari, Chrome, Firefox, Edge) show the tile itself, not a square on a plate.
- apple-touch-icon(.precomposed).png 180×180: full-bleed and opaque; iOS / macOS round it themselves.
- icon-192 / icon-512 (rounded, purpose "any") and icon-maskable-512 (full bleed, safe zone respected: the A
  already sits inside the central 80 %) for site.webmanifest / the console manifest.
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'brand' / 'app-icon-1024.png'
PUBLIC = ROOT / 'public'
CONSOLE = ROOT / 'console' / 'static' / 'console' / 'icons'
RADIUS = 0.225  # corner radius as a share of the side (close to Apple's icon corners)


def rounded(img, size):
    big = 2048
    square = img.resize((big, big), Image.LANCZOS)
    mask = Image.new('L', (big * 2, big * 2), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, big * 2 - 1, big * 2 - 1), radius=int(big * 2 * RADIUS), fill=255)
    mask = mask.resize((big, big), Image.LANCZOS)
    out = Image.new('RGBA', (big, big), (0, 0, 0, 0))
    out.paste(square, (0, 0), mask)
    return out.resize((size, size), Image.LANCZOS)


def full(img, size):
    return img.convert('RGB').resize((size, size), Image.LANCZOS)


def main():
    img = Image.open(SRC).convert('RGBA')
    assert img.size[0] == img.size[1] and img.size[0] >= 512, 'need a square source ≥ 512 px'
    PUBLIC.mkdir(exist_ok=True)
    CONSOLE.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64, 128, 256]
    rounded(img, 256).save(PUBLIC / 'favicon.ico', sizes=[(s, s) for s in sizes])
    for s in (32, 96, 192, 512):
        rounded(img, s).save(PUBLIC / f'icon-{s}.png', optimize=True)
    full(img, 512).save(PUBLIC / 'icon-maskable-512.png', optimize=True)
    for name in ('apple-touch-icon.png', 'apple-touch-icon-precomposed.png'):
        full(img, 180).save(PUBLIC / name, optimize=True)
    for s in (192, 512):
        rounded(img, s).save(CONSOLE / f'icon-{s}.png', optimize=True)
    full(img, 512).save(CONSOLE / 'icon-maskable-512.png', optimize=True)
    print('icons written')


if __name__ == '__main__':
    main()
