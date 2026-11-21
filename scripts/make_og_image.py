"""The home page's Open Graph image (1200×630): uv run python scripts/make_og_image.py → home/static/home/img/og.png.
Brand gradient, the app icon and one line of text; no web fonts (DejaVu / the system's bold sans)."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'home' / 'static' / 'home' / 'img' / 'og.png'


def font(size):
    for name in ('DejaVuSans-Bold.ttf', '/System/Library/Fonts/Supplemental/Arial Bold.ttf',
                 '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    w, h = 1200, 630
    img = Image.new('RGB', (w, h))
    top, bottom = (0x43, 0x1F, 0x86), (0x7B, 0x4F, 0xD6)
    px = img.load()
    for y in range(h):
        for x in range(w):
            t = (x / w * 0.6 + y / h * 0.4)
            px[x, y] = tuple(int(a + (b - a) * t) for a, b in zip(top, bottom))
    icon = Image.open(ROOT / 'brand' / 'app-icon-1024.png').convert('RGBA').resize((180, 180))
    mask = Image.new('L', icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, 179, 179), radius=40, fill=255)
    img.paste(icon, (90, 90), mask)
    d = ImageDraw.Draw(img)
    d.text((90, 320), 'Your event, live on every', font=font(64), fill='white')
    d.text((90, 400), 'attendee’s phone in a minute.', font=font(64), fill='white')
    d.text((90, 520), 'attnd.app · maps, schedules and updates', font=font(30), fill=(225, 214, 255))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    img.save(OUT, optimize=True)
    print(OUT, OUT.stat().st_size)


if __name__ == '__main__':
    main()
