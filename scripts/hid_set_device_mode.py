#!/usr/bin/env python3
"""Send openrazer-format razer_report feature report to Joro HID collections
to set device mode (fn/mm). Iterates every available Joro col and tries both
SetFeature and WriteOutput.

Razer report layout (90 bytes):
  [0] status
  [1] transaction_id (we use 0x1f)
  [2:4] remaining_packets (big-endian u16, 0)
  [4] protocol_type (0)
  [5] data_size
  [6] command_class
  [7] command_id
  [8:88] args[80]
  [88] crc (XOR of bytes [2:88])
  [89] reserved (0)

Transport: feature report with report_id = 0 → total 91 bytes.

For set_device_mode: class=0x00 cmd=0x04, args=[mode, 0].
Mode 0 = normal (MM), mode 3 = driver (Fn-primary).
"""
import hid, sys, time

VID = 0x1532  # Razer's vendor id — ALTERNATE
VID_ALT = 0x068e  # what the HID collection paths show
PID = 0x02ce

def razer_report(cclass, cid, args):
    buf = bytearray(90)
    buf[0] = 0  # status
    buf[1] = 0x1f  # transaction id
    # buf[2:4] remaining = 0
    buf[4] = 0  # protocol type
    buf[5] = len(args)
    buf[6] = cclass
    buf[7] = cid
    for i, a in enumerate(args):
        buf[8 + i] = a
    # CRC = XOR of bytes 2..88 (exclusive)
    crc = 0
    for i in range(2, 88):
        crc ^= buf[i]
    buf[88] = crc
    buf[89] = 0
    return bytes(buf)

def hex_dump(b):
    return " ".join(f"{x:02x}" for x in b[:32]) + ("..." if len(b) > 32 else "")

def try_all_joro(mode: int):
    """Send set_device_mode(mode, 0) as a feature report on every Joro
    collection (across both possible vendor IDs)."""
    report = razer_report(0x00, 0x04, [mode, 0])
    feature_buf = bytes([0x00]) + report  # report id 0 prepended
    print(f"Target mode: {mode}")
    print(f"Report (90B): {hex_dump(report)}")

    enumerated = hid.enumerate()
    matches = [d for d in enumerated if d.get('vendor_id') in (VID, VID_ALT) and d.get('product_id') == PID]
    if not matches:
        print("No Joro HID devices found. Enumerated vendor/products:")
        seen = set()
        for d in enumerated:
            k = (d.get('vendor_id'), d.get('product_id'))
            if k in seen: continue
            seen.add(k)
            print(f"  vid=0x{k[0]:04x} pid=0x{k[1]:04x} {d.get('product_string','?')}")
        return

    for d in matches:
        path = d['path']
        iface = d.get('interface_number')
        usage_page = d.get('usage_page')
        usage = d.get('usage')
        print(f"\n--- {path.decode('utf-8','replace') if isinstance(path,bytes) else path}")
        print(f"    iface={iface} usage_page=0x{usage_page or 0:04x} usage=0x{usage or 0:04x}")
        try:
            h = hid.device()
            h.open_path(path)
        except Exception as e:
            print(f"    open failed: {e}")
            continue
        try:
            try:
                n = h.send_feature_report(feature_buf)
                print(f"    send_feature_report returned {n}")
            except Exception as e:
                print(f"    send_feature_report failed: {e}")
            try:
                fb = bytearray(91)
                fb[0] = 0  # report id
                got = h.get_feature_report(0, 91)
                print(f"    get_feature_report[0] ({len(got)}B): {hex_dump(bytes(got))}")
            except Exception as e:
                print(f"    get_feature_report failed: {e}")
        finally:
            h.close()

if __name__ == '__main__':
    mode = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    try_all_joro(mode)
