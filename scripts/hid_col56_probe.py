#!/usr/bin/env python3
"""Probe col05 and col06 (vendor collections, usage=0 usage_page=1) on Joro
for any writable feature/output report. These are the Razer vendor channels.
"""
import hid, sys

VID = 0x068e
PID = 0x02ce

def main():
    cols = [d for d in hid.enumerate() if d.get('vendor_id')==VID and d.get('product_id')==PID]
    # col05 and col06 are the ones with usage_page=1 usage=0
    vendors = [d for d in cols if d.get('usage_page')==1 and d.get('usage')==0]
    print(f"Found {len(vendors)} vendor collections (usage_page=1 usage=0)")
    for i, d in enumerate(vendors):
        p = d['path']
        print(f"\n--- vendor col #{i}: {p}")
        h = hid.device()
        try:
            h.open_path(p)
        except Exception as e:
            print(f"  open err: {e}"); continue
        try:
            # Probe each report ID with a distinctive 2-byte payload and
            # then GET back to see if the device echoes/accepts
            for rid in range(0, 10):
                for sz in [2, 4, 8, 11, 16, 32]:
                    buf = bytearray(sz)
                    buf[0] = rid
                    if sz >= 2: buf[1] = 0xa5
                    if sz >= 3: buf[2] = 0x5a
                    try:
                        n = h.send_feature_report(bytes(buf))
                    except Exception:
                        n = -999
                    try:
                        g = h.get_feature_report(rid, sz)
                        gx = ' '.join(f'{x:02x}' for x in g)
                    except Exception as e:
                        gx = f'ERR'
                    if n > 0 and gx != 'ERR':
                        print(f"  rid={rid} sz={sz:2d} send={n:2d} GET={gx}")
        finally:
            h.close()

if __name__ == '__main__':
    main()
