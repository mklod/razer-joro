#!/usr/bin/env python3
"""Exhaustive HID probe on Joro. Tries feature + output reports on every
report ID and every collection. Works in ONE-STEP mode so the user can
run it, observe F5 behavior, and we pin down the right byte.

Usage: python hid_wide_probe.py <mode>
  mode is a single int 0..255; becomes the first data byte after report id.
  (zero trailing padding up to the length each report supports)

We iterate over collections. For each, we try:
  - send_feature_report with len 2,3,4,8,16,32,64,90
  - write (output) with len 2,3,4,8,16,32,64,90
on report IDs 1..7.

For each attempt that's accepted (ret > 0), print it. Otherwise silent.
"""
import hid, sys

VID = 0x068e
PID = 0x02ce

def joro_collections():
    out = []
    for d in hid.enumerate():
        if d.get('vendor_id')==VID and d.get('product_id')==PID:
            out.append(d)
    return out

def main():
    mode = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0
    cols = joro_collections()
    print(f"{len(cols)} Joro collections; mode byte = 0x{mode:02x}")
    sizes = [2, 3, 4, 8, 16, 32, 64, 90]
    for d in cols:
        path = d['path']
        up = d.get('usage_page', 0)
        u = d.get('usage', 0)
        print(f"\n== up=0x{up:04x} u=0x{u:04x} ==")
        h = hid.device()
        try:
            h.open_path(path)
        except Exception as e:
            print(f"  open err: {e}"); continue
        try:
            for rid in range(1, 8):
                for sz in sizes:
                    # Build [rid, mode, 0, 0, ... total sz bytes]
                    buf = bytearray(sz)
                    buf[0] = rid
                    if sz >= 2: buf[1] = mode
                    # Feature
                    try:
                        n = h.send_feature_report(bytes(buf))
                        if n > 0:
                            print(f"  FEATURE ok  rid={rid} sz={sz}")
                    except Exception:
                        pass
                    # Output
                    try:
                        n = h.write(bytes(buf))
                        if n > 0:
                            print(f"  OUTPUT  ok  rid={rid} sz={sz}")
                    except Exception:
                        pass
        finally:
            h.close()

if __name__ == '__main__':
    main()
