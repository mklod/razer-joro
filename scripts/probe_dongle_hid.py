"""
probe_dongle_hid.py — exhaustive probe of every HID path on the Razer
multi-device dongle (PID 0x009C). Tries BOTH Set_Feature_Report AND
Output Report for each path, since some Razer firmware variants accept
Protocol30 only via interrupt-OUT.

Distinct color per *successful* write attempt — final Joro color identifies
the winning (path, method) tuple.

Usage:
    python probe_dongle_hid.py
"""

import hid
import time
import sys

VID = 0x1532
PID = 0x009C

PACKET_SIZE = 90

# Distinct colors. We assign one to each successful write attempt only.
COLOR_POOL = [
    ("RED",     0xFF, 0x00, 0x00),
    ("GREEN",   0x00, 0xFF, 0x00),
    ("BLUE",    0x00, 0x00, 0xFF),
    ("YELLOW",  0xFF, 0xFF, 0x00),
    ("MAGENTA", 0xFF, 0x00, 0xFF),
    ("CYAN",    0x00, 0xFF, 0xFF),
    ("ORANGE",  0xFF, 0x80, 0x00),
    ("WHITE",   0xFF, 0xFF, 0xFF),
    ("PURPLE",  0x80, 0x00, 0xFF),
    ("PINK",    0xFF, 0x40, 0xC0),
    ("LIME",    0x80, 0xFF, 0x00),
    ("TEAL",    0x00, 0x80, 0x80),
    ("ROSE",    0xFF, 0x00, 0x80),
    ("AZURE",   0x00, 0x80, 0xFF),
    ("OLIVE",   0x80, 0x80, 0x00),
    ("MAROON",  0x80, 0x00, 0x00),
    ("NAVY",    0x00, 0x00, 0x80),
    ("FOREST",  0x00, 0x80, 0x00),
    ("AMBER",   0xFF, 0xC0, 0x00),
    ("MINT",    0x00, 0xFF, 0x80),
]

_trans_id = 0


def next_trans_id():
    global _trans_id
    _trans_id = (_trans_id + 1) & 0xFF
    if _trans_id in (0x00, 0xFF):
        _trans_id = 0x01
    return _trans_id


def build_set_static_color(r, g, b):
    pkt = bytearray(PACKET_SIZE)
    pkt[0x00] = 0x00
    pkt[0x01] = next_trans_id()
    pkt[0x05] = 9
    pkt[0x06] = 0x0F
    pkt[0x07] = 0x02
    args = [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, r, g, b]
    pkt[0x08:0x08 + len(args)] = args
    crc = 0
    for b_ in pkt[2:88]:
        crc ^= b_
    pkt[0x58] = crc
    return bytes(pkt)


def main():
    devices = list(hid.enumerate(VID, PID))
    print(f"Found {len(devices)} HID interfaces under VID 0x{VID:04X} PID 0x{PID:04X}\n")
    if not devices:
        return 1

    print(f"{'idx':>3} {'usage_page':>10} {'usage':>6} {'iface':>5} path")
    print("-" * 100)
    for i, d in enumerate(devices):
        path = d['path'].decode('latin-1', errors='replace') if isinstance(d['path'], bytes) else d['path']
        print(f"{i:>3} 0x{d['usage_page']:08X} 0x{d['usage']:04X} {d['interface_number']:>5} {path}")
    print()

    # We'll try BOTH methods on every path. (path_idx, method) -> color
    attempts = []
    color_idx = 0

    print("Probing each path with both Set_Feature and Output Report. 0.8s between writes.\n")
    time.sleep(1.0)

    for i, d in enumerate(devices):
        path = d['path']
        for method in ("feature", "output"):
            if color_idx >= len(COLOR_POOL):
                print("  (color pool exhausted, stopping)")
                break
            cname, r, g, b = COLOR_POOL[color_idx]
            try:
                h = hid.device()
                h.open_path(path)
                pkt = build_set_static_color(r, g, b)
                if method == "feature":
                    n = h.send_feature_report(b'\x00' + pkt)
                else:
                    n = h.write(b'\x00' + pkt)
                h.close()
                ok = n > 0
                attempts.append((i, method, cname, ok, None, d))
                marker = "OK " if ok else "ZERO"
                print(f"  [{i:>2}.{method[:3]}] -> {cname:7s}  iface={d['interface_number']} usage_page=0x{d['usage_page']:04X}  wrote={n} [{marker}]")
                if ok:
                    color_idx += 1
                    time.sleep(0.8)
            except Exception as e:
                attempts.append((i, method, cname, False, str(e), d))
                # don't burn the color slot on failure
                print(f"  [{i:>2}.{method[:3]}]    skip                                     err: {e}")
        if color_idx >= len(COLOR_POOL):
            break

    print()
    print("=" * 60)
    print(f"Used {color_idx} colors total. Tell me Joro's CURRENT color.")
    print("=" * 60)
    print("\nSuccessful writes (in order):")
    for (idx, method, cname, ok, err, d) in attempts:
        if ok:
            print(f"  [{idx:>2}.{method:7s}] {cname:>8s}  iface={d['interface_number']} "
                  f"usage_page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
