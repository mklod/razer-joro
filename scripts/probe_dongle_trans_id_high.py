"""
Test hypothesis: high bit in trans_id routes Protocol30 to keyboard slot
of the multi-device dongle (vs mouse).

Sends set_static_color(BLUE) to MI_00 with trans_id=0x80, then
set_static_color(YELLOW) with trans_id=0x81. If Joro turns blue/yellow,
hypothesis confirmed.
"""
import hid, time

VID = 0x1532
PID = 0x009C

def build_set_static_color(trans_id, r, g, b):
    pkt = bytearray(90)
    pkt[0x00] = 0x00
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


def find_mi00():
    for d in hid.enumerate(VID, PID):
        if d['interface_number'] == 0:
            return d
    return None


def main():
    d = find_mi00()
    if not d:
        print("MI_00 not found"); return
    path = d['path']
    print(f"Opening MI_00: {path.decode('latin-1') if isinstance(path, bytes) else path}\n")

    h = hid.device()
    h.open_path(path)

    tests = [
        ("BLUE",   0x80, 0x00, 0x00, 0xFF),
        ("YELLOW", 0x81, 0xFF, 0xFF, 0x00),
        ("MAGENTA",0x82, 0xFF, 0x00, 0xFF),
    ]
    for label, tid, r, g, b in tests:
        pkt = build_set_static_color(tid, r, g, b)
        wrote = h.send_feature_report(b'\x00' + pkt)
        print(f"  trans_id=0x{tid:02x} {label:8s} wrote={wrote}  hex(0..16)={pkt[:16].hex(' ')}")
        time.sleep(1.5)

    h.close()
    print("\nFinal Joro color?")
    print("  BLUE/YELLOW/MAGENTA -> hypothesis confirmed (high-bit trans_id routes to keyboard)")
    print("  unchanged           -> different mechanism, need deeper analysis")


if __name__ == "__main__":
    main()
