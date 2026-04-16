#!/usr/bin/env python3
"""Generate the Joro daemon tray/window icons.

As of 2026-04-15 the daemon uses Microsoft's On-Screen Keyboard icon
(`C:\\Windows\\System32\\osk.exe` → extracted via
`assets/extract_osk_icon.ps1`) for the tray/window because our
hand-rendered keyboard glyphs kept looking mushy at 16px regardless
of how many times we tweaked the PIL recipe. Microsoft's designers
did a better job and the icon is perfectly legible at every size.

License note: osk.exe is a Windows system binary; its icon is
Microsoft copyright. This project uses it privately as a Synapse
replacement for personal use. Don't redistribute binaries embedding
Microsoft's icon without understanding the legal implications.

This script now does two things:

1. Post-processes the extracted `osk_large.png` (32x32) into
   `joro_icon_32.png` (tray) and `joro_icon_disconnected_32.png`
   (tray, disconnected variant — greyscale + red LED dot).
2. Keeps rendering the legacy hand-drawn PIL keyboard as a fallback
   in `joro_icon.ico` + `joro_icon_disconnected.ico` for the window
   title-bar until we extract a multi-size osk ICO.

Run: `python assets/gen_icon.py` after `extract_osk_icon.ps1` has
populated the assets with the osk_*.png sources.
"""
from PIL import Image, ImageDraw
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Every size we want in the fallback ICO. Windows picks the closest
# match for the current taskbar DPI, so covering the common sizes is
# enough. Legacy PIL path only — tray uses the osk PNGs.
SIZES = [16, 20, 24, 32, 40, 48, 64, 128, 256]


def draw_keyboard(size: int, connected: bool = True) -> Image.Image:
    """Render the keyboard icon at a specific pixel size. Everything
    scales proportionally, with minimum line widths of 1px so small
    sizes still render crisp hard edges."""
    img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    # Core colors
    body_color = (248, 248, 248, 255) if connected else (140, 140, 140, 255)
    # "Keys" are cut-outs drawn in a dark shade over the body so each
    # individual key shape is visible. Transparent cut-outs would show
    # through to whatever's behind the icon on the taskbar, which is
    # unpredictable — a dark fill is consistent.
    key_color = (24, 24, 24, 255)
    led_color = (255, 64, 64, 255)

    # Keyboard body dimensions — fills ~86% width, ~56% height.
    kb_w = round(size * 0.86)
    kb_h = round(size * 0.56)
    kb_x = (size - kb_w) // 2
    kb_y = (size - kb_h) // 2 + round(size * 0.04)  # shift down so LED fits above
    radius = max(1, round(size * 0.08))

    d.rounded_rectangle(
        (kb_x, kb_y, kb_x + kb_w - 1, kb_y + kb_h - 1),
        radius=radius,
        fill=body_color,
    )

    # Interior padding for the key layout
    pad = max(1, round(size * 0.035))
    ix1 = kb_x + pad * 2
    iy1 = kb_y + pad * 2
    ix2 = kb_x + kb_w - pad * 2
    iy2 = kb_y + kb_h - pad * 2
    iw = ix2 - ix1
    ih = iy2 - iy1

    # Two rows of 5 keys each + a wider spacebar row at the bottom.
    # Gap and key dimensions computed from interior size so they scale
    # cleanly. Minimum 1px to survive downscaling.
    rows = 2
    cols = 5
    gap = max(1, round(size * 0.02))
    key_r = max(1, round(size * 0.025))

    # Vertical layout: top 60% is the key rows, bottom 40% is the
    # spacebar zone (with a gap between).
    row_zone_h = round(ih * 0.58)
    space_zone_h = ih - row_zone_h - gap
    key_h = (row_zone_h - (rows - 1) * gap) / rows
    key_w = (iw - (cols - 1) * gap) / cols

    for r in range(rows):
        for c in range(cols):
            x1 = ix1 + round(c * (key_w + gap))
            y1 = iy1 + round(r * (key_h + gap))
            x2 = x1 + round(key_w)
            y2 = y1 + round(key_h)
            d.rounded_rectangle((x1, y1, x2, y2), radius=key_r, fill=key_color)

    # Spacebar — narrower than the row zone so it looks like a real
    # space bar with mod keys beside it.
    sb_margin = round(iw * 0.22)
    sb_x1 = ix1 + sb_margin
    sb_y1 = iy1 + row_zone_h + gap
    sb_x2 = ix2 - sb_margin
    sb_y2 = sb_y1 + space_zone_h
    d.rounded_rectangle((sb_x1, sb_y1, sb_x2, sb_y2), radius=key_r, fill=key_color)

    # Disconnected: red LED dot above the top-right corner of the keyboard.
    if not connected:
        dot_r = max(1, round(size * 0.075))
        dx = kb_x + kb_w - dot_r * 2 - 2
        dy = max(0, kb_y - dot_r * 2 - 1)
        d.ellipse(
            (dx, dy, dx + dot_r * 2, dy + dot_r * 2),
            fill=led_color,
        )

    return img


def save_ico(variant: str, connected: bool) -> None:
    """Render every size and bundle them into a single multi-size ICO."""
    out = HERE / f'joro_icon{"" if connected else "_disconnected"}.ico'

    # PIL wants the base image first, then additional sizes via
    # append_images. Use the largest size as the primary.
    images = [draw_keyboard(s, connected=connected) for s in SIZES]
    images.sort(key=lambda im: im.width, reverse=True)
    primary = images[0]
    extras = images[1:]

    primary.save(
        out,
        format='ICO',
        sizes=[(im.width, im.height) for im in images],
        append_images=extras,
    )
    print(f'Wrote {out} ({out.stat().st_size} bytes, sizes={SIZES})')


def build_tray_pngs_from_osk() -> None:
    """Post-process the extracted osk.exe icons into our tray PNGs.
    Reads `osk_large.png` (32x32) and derives both the connected and
    disconnected variants from it.

    Connected: copy straight through.
    Disconnected: desaturate to grey + stamp a red LED dot in the
    top-right corner so the user can see at a glance whether the
    daemon is talking to the keyboard.
    """
    src = HERE / 'osk_large.png'
    if not src.exists():
        print(f'WARN: {src} missing — run extract_osk_icon.ps1 first')
        return

    base = Image.open(src).convert('RGBA')
    # Connected: unchanged osk icon
    (HERE / 'joro_icon_32.png').write_bytes(src.read_bytes())
    print(f'Wrote joro_icon_32.png ({base.width}x{base.height}, from osk_large.png)')

    # Disconnected: desaturate + LED dot overlay
    disc = base.copy()
    pixels = disc.load()
    for y in range(disc.height):
        for x in range(disc.width):
            r, g, b, a = pixels[x, y]
            if a == 0:
                continue
            grey = int(0.30 * r + 0.59 * g + 0.11 * b)
            # Dim a bit more so it reads as "off"
            grey = int(grey * 0.7)
            pixels[x, y] = (grey, grey, grey, a)

    d = ImageDraw.Draw(disc)
    dot_r = 5
    dx = disc.width - dot_r * 2 - 1
    dy = 1
    d.ellipse((dx, dy, dx + dot_r * 2, dy + dot_r * 2), fill=(255, 64, 64, 255))

    out = HERE / 'joro_icon_disconnected_32.png'
    disc.save(out, format='PNG')
    print(f'Wrote {out.name} ({disc.width}x{disc.height}, desaturated + LED dot)')


if __name__ == '__main__':
    # Legacy hand-drawn PIL keyboard icons — kept as fallback /
    # window title-bar source until we have a multi-size osk ICO.
    save_ico('connected', connected=True)
    save_ico('disconnected', connected=False)

    # Authoritative tray icons: derived from osk.exe.
    build_tray_pngs_from_osk()
