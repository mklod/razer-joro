#!/usr/bin/env python3
"""Send openrazer 90-byte razer_report as a HID Feature Report to Joro
across all report IDs (0..6) and every collection. This tests the classic
USB Razer Protocol path to see if Joro's BLE firmware accepts it.

Args: <class_hex> <cmd_hex> <arg0_hex> <arg1_hex>
Example: hid_razer_90b.py 00 04 00 00   (set_device_mode(0))
         hid_razer_90b.py 00 04 03 00   (set_device_mode(3))
"""
import hid, sys

VID = 0x068e
PID = 0x02ce

def razer_report(cclass, cid, args):
    buf = bytearray(90)
    buf[0] = 0x00       # status
    buf[1] = 0x1f       # transaction id
    # 2,3 remaining packets = 0
    buf[4] = 0x00       # protocol type
    buf[5] = len(args)  # data_size
    buf[6] = cclass     # command_class
    buf[7] = cid        # command_id
    for i, a in enumerate(args):
        buf[8 + i] = a
    # CRC = XOR of bytes [2..87]
    crc = 0
    for i in range(2, 88):
        crc ^= buf[i]
    buf[88] = crc
    buf[89] = 0x00
    return bytes(buf)

def main():
    c = int(sys.argv[1], 16)
    m = int(sys.argv[2], 16)
    a0 = int(sys.argv[3], 16) if len(sys.argv) > 3 else 0
    a1 = int(sys.argv[4], 16) if len(sys.argv) > 4 else 0
    rpt = razer_report(c, m, [a0, a1])
    print(f"razer_report class=0x{c:02x} cmd=0x{m:02x} args=[0x{a0:02x},0x{a1:02x}]")
    print(f"  bytes: {' '.join(f'{x:02x}' for x in rpt[:16])}...crc=0x{rpt[88]:02x}")

    for d in hid.enumerate():
        if d.get('vendor_id') != VID or d.get('product_id') != PID: continue
        up = d.get('usage_page', 0)
        u = d.get('usage', 0)
        h = hid.device()
        try:
            h.open_path(d['path'])
        except Exception as e:
            continue
        try:
            for rid in range(0, 7):
                buf = bytes([rid]) + rpt
                try:
                    n = h.send_feature_report(buf)
                    print(f"  up=0x{up:04x} u=0x{u:04x} rid={rid} FEATURE send={n}")
                except Exception as e:
                    pass
        finally:
            h.close()

if __name__ == '__main__':
    main()
