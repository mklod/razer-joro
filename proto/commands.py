# proto/commands.py
# Last modified: 2026-04-09--1530
"""
High-level Razer Joro commands.

Command reference from openrazer PR #2683 + live hardware testing:
  Set static color:  class=0x0F, id=0x02, size=9
                     args=[VARSTORE, BACKLIGHT_LED, effect=0x01, 0x00, 0x00, 0x01, R, G, B]
  Set brightness:    class=0x0F, id=0x04, size=3
                     args=[VARSTORE, BACKLIGHT_LED, brightness]
  Get brightness:    class=0x0F, id=0x84, size=1
                     args=[VARSTORE] -> response args=[VARSTORE, BACKLIGHT_LED, brightness]
  Get firmware:      class=0x00, id=0x81, size=0
  Set device mode:   class=0x00, id=0x04, size=2
                     args=[mode, param] (mode 0x03 = driver mode)
  Effect none:       class=0x0F, id=0x02, size=6
                     args=[VARSTORE, BACKLIGHT_LED, 0x00, 0x00, 0x00, 0x00]
"""

from razer_packet import (
    build_packet, parse_packet,
    VARSTORE, BACKLIGHT_LED, STATUS_OK, STATUS_NOT_SUPPORTED,
)


def get_firmware(transport) -> str:
    """Get firmware version string."""
    pkt = build_packet(0x00, 0x81, 0)
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    _check(p, "get_firmware")
    major = p["args"][0] if len(p["args"]) > 0 else 0
    minor = p["args"][1] if len(p["args"]) > 1 else 0
    return f"{major}.{minor}"


def set_device_mode(transport, mode: int, param: int = 0) -> dict:
    """Set device mode. mode=0x03 for driver mode."""
    pkt = build_packet(0x00, 0x04, 2, bytes([mode, param]))
    resp = transport.send_packet(pkt)
    return parse_packet(resp)


def get_brightness(transport) -> int:
    """Get backlight brightness (0-255)."""
    pkt = build_packet(0x0F, 0x84, 1, bytes([VARSTORE]))
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    _check(p, "get_brightness")
    # Response args: [VARSTORE, BACKLIGHT_LED, brightness]
    return p["args"][2] if len(p["args"]) >= 3 else 0


def set_brightness(transport, brightness: int) -> dict:
    """Set backlight brightness (0-255)."""
    pkt = build_packet(0x0F, 0x04, 3, bytes([VARSTORE, BACKLIGHT_LED, brightness & 0xFF]))
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    _check(p, "set_brightness")
    return p


def set_static_color(transport, r: int, g: int, b: int) -> dict:
    """Set static backlight color."""
    args = bytes([VARSTORE, BACKLIGHT_LED, 0x01, 0x00, 0x00, 0x01,
                  r & 0xFF, g & 0xFF, b & 0xFF])
    pkt = build_packet(0x0F, 0x02, 9, args)
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    _check(p, "set_static_color")
    return p


def set_effect_none(transport) -> dict:
    """Turn off backlight (none effect)."""
    args = bytes([VARSTORE, BACKLIGHT_LED, 0x00, 0x00, 0x00, 0x00])
    pkt = build_packet(0x0F, 0x02, 6, args)
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    _check(p, "set_effect_none")
    return p


def _check(parsed: dict, cmd_name: str):
    """Check response status, raise on error."""
    s = parsed["status"]
    if s == STATUS_OK:
        return
    if s == STATUS_NOT_SUPPORTED:
        raise IOError(f"{cmd_name}: not supported (status 0x05)")
    raise IOError(f"{cmd_name}: status 0x{s:02X}")
