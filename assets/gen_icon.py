#!/usr/bin/env python3
"""Generate the Joro daemon tray/window icon as a multi-size .ico file.

Run this once to (re)generate assets/joro_icon.ico. The daemon loads the
resulting file at runtime via Icon::from_path().

Design: a minimal white keyboard outline on transparent background with
rounded corners, two rows of small rounded key blocks, and a wider
spacebar block in the bottom row. Monochromatic so it adapts to both
light and dark taskbars. A "disconnected" variant adds a red LED dot.
"""
from PIL import Image, ImageDraw, ImageFilter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICO_OUT = HERE / 'joro_icon.ico'

# Render at 256px then let Windows downscale — ICO files support multiple sizes
CANVAS = 256


def draw_keyboard(connected: bool = True) -> Image.Image:
    img = Image.new('RGBA', (CANVAS, CANVAS), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)

    outline_color = (255, 255, 255, 255) if connected else (150, 150, 150, 255)
    key_color    = (235, 235, 235, 255) if connected else (120, 120, 120, 255)

    # Keyboard outer shape: rounded rectangle centered
    kb_w, kb_h = 208, 128
    kb_x = (CANVAS - kb_w) // 2
    kb_y = (CANVAS - kb_h) // 2
    radius = 14
    outline_w = 6
    d.rounded_rectangle(
        (kb_x, kb_y, kb_x + kb_w, kb_y + kb_h),
        radius=radius,
        outline=outline_color,
        width=outline_w,
    )

    # Interior padding inside the outline
    pad = outline_w + 14
    ix1 = kb_x + pad
    iy1 = kb_y + pad
    ix2 = kb_x + kb_w - pad
    iy2 = kb_y + kb_h - pad
    iw = ix2 - ix1
    ih = iy2 - iy1

    # 2 rows of 6 key blocks
    rows = 2
    cols = 6
    gap = 8
    key_w = (iw - (cols - 1) * gap) / cols
    # Rows take 40% of interior, spacebar row takes the rest below
    row_zone = ih * 0.55
    key_h = (row_zone - (rows - 1) * gap) / rows
    key_r = 4

    for r in range(rows):
        for c in range(cols):
            x1 = ix1 + c * (key_w + gap)
            y1 = iy1 + r * (key_h + gap)
            x2 = x1 + key_w
            y2 = y1 + key_h
            d.rounded_rectangle((x1, y1, x2, y2), radius=key_r, fill=key_color)

    # Spacebar: wider block in bottom row
    sb_h = ih * 0.30
    sb_x1 = ix1 + iw * 0.15
    sb_y1 = iy1 + row_zone + gap * 2
    sb_x2 = ix2 - iw * 0.15
    sb_y2 = sb_y1 + sb_h
    d.rounded_rectangle((sb_x1, sb_y1, sb_x2, sb_y2), radius=key_r, fill=key_color)

    # Disconnected: red LED dot above the keyboard (top-right area)
    if not connected:
        dot_r = 14
        dx = kb_x + kb_w - dot_r * 2 - 4
        dy = kb_y - dot_r * 2 - 6
        d.ellipse(
            (dx, dy, dx + dot_r * 2, dy + dot_r * 2),
            fill=(255, 64, 64, 255),
        )

    return img


def save_ico(img: Image.Image, path: Path):
    # Save as multi-resolution ICO so Windows picks the right size per DPI
    sizes = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48),
             (64, 64), (128, 128), (256, 256)]
    img.save(path, format='ICO', sizes=sizes)
    print(f'Wrote {path} ({path.stat().st_size} bytes)')


if __name__ == '__main__':
    connected = draw_keyboard(connected=True)
    disconnected = draw_keyboard(connected=False)
    save_ico(connected, HERE / 'joro_icon.ico')
    save_ico(disconnected, HERE / 'joro_icon_disconnected.ico')
