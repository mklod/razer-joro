# proto/test_lighting.py
# Last modified: 2026-04-09--1530
"""
Interactive lighting test — set color and brightness on Joro.

Usage:
  python test_lighting.py                    # default: orange, brightness 200
  python test_lighting.py FF0000             # red
  python test_lighting.py FF0000 128         # red, half brightness
"""

import sys
from usb_transport import UsbTransport
from commands import (
    get_firmware, set_brightness, get_brightness, set_static_color, set_effect_none,
)


def main():
    color_hex = sys.argv[1] if len(sys.argv) > 1 else "FF6600"
    brightness = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    r = int(color_hex[0:2], 16)
    g = int(color_hex[2:4], 16)
    b = int(color_hex[4:6], 16)

    with UsbTransport() as t:
        fw = get_firmware(t)
        print(f"Firmware: {fw}")

        cur = get_brightness(t)
        print(f"Current brightness: {cur}")

        print(f"\nSetting brightness to {brightness}...")
        set_brightness(t, brightness)

        print(f"Setting color to #{color_hex} (R={r} G={g} B={b})...")
        set_static_color(t, r, g, b)

        new_b = get_brightness(t)
        print(f"Verify brightness: {new_b}")
        print("\nCheck the keyboard — backlight should be the requested color.")


if __name__ == "__main__":
    main()
