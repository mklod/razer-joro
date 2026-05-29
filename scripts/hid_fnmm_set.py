#!/usr/bin/env python3
"""Single-shot: send 3-byte feature report [1, b0, b1] to Joro's keyboard
collection (col01) and print GET result. Use to pin down the exact byte
combo that controls fn/mm.

Usage: python hid_fnmm_set.py <b0 hex> <b1 hex>
Example: python hid_fnmm_set.py 00 00
"""
import hid, sys

VID = 0x068e
PID = 0x02ce

def find_kbd():
    for d in hid.enumerate():
        if d.get('vendor_id')==VID and d.get('product_id')==PID \
           and d.get('usage_page')==0x01 and d.get('usage')==0x06:
            return d['path']
    return None

def main():
    b0 = int(sys.argv[1], 16)
    b1 = int(sys.argv[2], 16)
    path = find_kbd()
    if not path: print("no kbd"); return
    h = hid.device()
    h.open_path(path)
    try:
        n = h.send_feature_report(bytes([1, b0, b1]))
        g = h.get_feature_report(1, 16)
        print(f"SEND [1, {b0:02x}, {b1:02x}] -> {n}; GET = {' '.join(f'{x:02x}' for x in g)}")
    finally:
        h.close()

if __name__ == '__main__':
    main()
