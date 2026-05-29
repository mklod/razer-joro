#!/usr/bin/env python3
"""Try sending 3-byte feature reports to Joro HID collection col01 (keyboard)
with report ID 1 — the descriptor shows a 2-byte feature report on that report ID.
Sweep plausible values and report results so the user can observe which
combination flips fn/mm.
"""
import hid, sys, time

VID = 0x068e
PID = 0x02ce

def hx(b): return " ".join(f"{x:02x}" for x in b)

def enumerate_kbd():
    """Find col01 (keyboard usage_page=1 usage=6) on Joro."""
    for d in hid.enumerate():
        if d.get('vendor_id') != VID or d.get('product_id') != PID: continue
        if d.get('usage_page') == 0x01 and d.get('usage') == 0x06:
            return d['path']
    return None

def send_and_report(label, buf):
    path = enumerate_kbd()
    if not path:
        print("no Joro kbd collection"); return
    h = hid.device()
    try:
        h.open_path(path)
        n = h.send_feature_report(bytes(buf))
        # Also try get
        try:
            g = h.get_feature_report(1, 32)
            gx = hx(bytes(g))
        except Exception as e:
            gx = f"ERR {e}"
        print(f"{label:35s}  send={n}  GET(id=1)={gx}")
    except Exception as e:
        print(f"{label:35s}  OPEN/SEND ERR: {e}")
    finally:
        h.close()

def main():
    print("Current GET of feature id=1:")
    send_and_report("(baseline)", None if False else [1, 0, 0])
    time.sleep(0.3)
    # Test combinations — report id 1, two data bytes
    cases = [
        ("fid=1 [00,00]", [1, 0x00, 0x00]),
        ("fid=1 [01,00]", [1, 0x01, 0x00]),
        ("fid=1 [00,01]", [1, 0x00, 0x01]),
        ("fid=1 [03,00]", [1, 0x03, 0x00]),
        ("fid=1 [00,03]", [1, 0x00, 0x03]),
        ("fid=1 [ff,ff]", [1, 0xff, 0xff]),
    ]
    for label, buf in cases:
        send_and_report(label, buf)
        time.sleep(0.4)

if __name__ == '__main__':
    main()
