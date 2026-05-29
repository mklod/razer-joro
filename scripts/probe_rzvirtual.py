"""
Enumerate every HID device (no VID/PID filter), look for paths matching
RZVIRTUAL or VID_068E, and try Set_Feature_Report on each. We also try
the captured Synapse packet verbatim (class=0x0F cmd=0x03 with ramp args)
plus a clean class=0x0F cmd=0x02 set-effect.
"""
import hid, time

# A complete capture of one Synapse "set color" packet (class=0x0F cmd=0x03)
SYNAPSE_RAMP_PKT = bytes.fromhex(
    "00 80 00 00 00 08 0f 03 00 00 00 00 00 80 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 "
    "00 00 00 00 00 00 00 00 84 00".replace(" ", "")
)
assert len(SYNAPSE_RAMP_PKT) == 90


def build_set_static_color(trans_id, r, g, b):
    pkt = bytearray(90)
    pkt[0x01] = trans_id
    pkt[0x05] = 9
    pkt[0x06] = 0x0F
    pkt[0x07] = 0x02
    args = [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, r, g, b]
    pkt[0x08:0x08 + len(args)] = args
    crc = 0
    for b_ in pkt[2:88]: crc ^= b_
    pkt[0x58] = crc
    return bytes(pkt)


def main():
    print("All HID devices in system:")
    devs = list(hid.enumerate(0, 0))
    interesting = []
    for d in devs:
        path = d['path'].decode('latin-1', errors='replace') if isinstance(d['path'], bytes) else d['path']
        if 'RZVIRTUAL' in path.upper() or 'VID_068E' in path.upper() or '009C' in path.upper():
            interesting.append((d, path))

    for i, (d, path) in enumerate(interesting):
        print(f"\n[{i:>2}] vid=0x{d['vendor_id']:04x} pid=0x{d['product_id']:04x} "
              f"usage_page=0x{d['usage_page']:04x} usage=0x{d['usage']:04x} iface={d['interface_number']}")
        print(f"     path: {path}")

    print(f"\nTotal interesting paths: {len(interesting)}\n")
    print(f"Trying Synapse-captured set-effect packet (class=0x0F cmd=0x03) on each...\n")

    for i, (d, path_s) in enumerate(interesting):
        path = d['path']
        try:
            h = hid.device()
            h.open_path(path)
            n_feat = h.send_feature_report(b'\x00' + SYNAPSE_RAMP_PKT)
            n_out = -1
            try:
                n_out = h.write(b'\x00' + SYNAPSE_RAMP_PKT)
            except Exception as e:
                n_out_err = str(e)
            h.close()
            print(f"  [{i:>2}] SET_FEATURE wrote={n_feat}  WRITE wrote={n_out}")
        except Exception as e:
            print(f"  [{i:>2}] OPEN FAILED: {e}")
        time.sleep(0.3)


if __name__ == "__main__":
    main()
