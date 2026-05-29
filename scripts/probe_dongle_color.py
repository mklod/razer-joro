"""
Brute-force discovery of the dongle's set-static-color command. We've
proven the brightness command works with LED_ID=0x00 correction; try the
same correction on set_static_color (class=0x0F cmd=0x02).

Strategy: send each variant with a distinct color, watch Joro for the
final color. The variant that wins identifies the format.
"""
import hid, time, sys

VID = 0x1532
PID = 0x009C


def build_pkt(trans_id, cls, cmd, args):
    pkt = bytearray(90)
    pkt[0x00] = 0x00
    pkt[0x01] = trans_id
    pkt[0x05] = len(args) & 0xFF
    pkt[0x06] = cls
    pkt[0x07] = cmd
    for i, a in enumerate(args):
        pkt[0x08 + i] = a
    crc = 0
    for b in pkt[2:88]: crc ^= b
    pkt[0x58] = crc
    return bytes(pkt)


def main():
    d = next((d for d in hid.enumerate(VID, PID) if d['interface_number'] == 0), None)
    if not d:
        print("MI_00 not found"); return 1
    h = hid.device(); h.open_path(d['path'])

    # Set brightness high so any color is clearly visible
    bright = build_pkt(0x80, 0x0F, 0x04, [0x01, 0x00, 0xFF])
    h.send_feature_report(b'\x00' + bright)
    time.sleep(0.5)

    variants = [
        # (label, R, G, B, args_factory)
        ("V1 cmd=0x02 LED=0x00 direct-USB style",  0xFF, 0x00, 0x00,
         lambda r,g,b: [0x01, 0x00, 0x01, 0x00, 0x00, 0x01, r, g, b]),
        ("V2 cmd=0x02 LED=0x05 direct-USB style",  0x00, 0xFF, 0x00,
         lambda r,g,b: [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, r, g, b]),
        ("V3 cmd=0x02 args=[0,0,RGB]",              0x00, 0x00, 0xFF,
         lambda r,g,b: [0x00, 0x00, r, g, b]),
        ("V4 cmd=0x02 args=[0,0,0x01,RGB]",         0xFF, 0xFF, 0x00,
         lambda r,g,b: [0x00, 0x00, 0x01, r, g, b]),
        ("V5 cmd=0x03 args=[VARSTORE,0,0,0,0,0,RGB]", 0xFF, 0x00, 0xFF,
         lambda r,g,b: [0x01, 0x00, 0x00, 0x00, 0x00, 0x00, r, g, b]),
        ("V6 cmd=0x03 args=[0,0,0,0,0,RGB]",         0x00, 0xFF, 0xFF,
         lambda r,g,b: [0x00, 0x00, 0x00, 0x00, 0x00, r, g, b]),
        ("V7 cmd=0x05 STATIC args=[VARSTORE,0,RGB]", 0xFF, 0x80, 0x00,
         lambda r,g,b: [0x01, 0x00, r, g, b]),
        ("V8 cmd=0x06 args=[VARSTORE,0,RGB]",        0x80, 0x00, 0xFF,
         lambda r,g,b: [0x01, 0x00, r, g, b]),
    ]

    print("Testing variants — watch Joro color. Final color identifies the winning variant.\n")
    time.sleep(2.0)

    for i, (label, r, g, b, factory) in enumerate(variants):
        tid = 0x81 + i
        cmd = 0x02
        if "cmd=0x03" in label: cmd = 0x03
        elif "cmd=0x05" in label: cmd = 0x05
        elif "cmd=0x06" in label: cmd = 0x06
        args = factory(r, g, b)
        pkt = build_pkt(tid, 0x0F, cmd, args)
        wrote = h.send_feature_report(b'\x00' + pkt)
        print(f"  [{i}] {label} -> RGB=({r:#04x},{g:#04x},{b:#04x})  cmd=0x{cmd:02x} args={args}  wrote={wrote}", flush=True)
        time.sleep(2.5)

    h.close()
    print("\nDone. What color did Joro end on? (matches the variant from the list)")


if __name__ == "__main__":
    sys.exit(main() or 0)
