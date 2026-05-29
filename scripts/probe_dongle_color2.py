"""
Refined color probe. V8 (cmd=0x06) appears to disable lighting — drop it.
Restore brightness first, then try fewer, more likely color variants with
big visible color jumps.
"""
import hid, time, sys

VID = 0x1532
PID = 0x009C


def build_pkt(trans_id, cls, cmd, args):
    pkt = bytearray(90)
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

    print("Restoring brightness to 100%...")
    h.send_feature_report(b'\x00' + build_pkt(0x80, 0x0F, 0x04, [0x01, 0x00, 0xFF]))
    time.sleep(1.0)

    # Each variant gets a maximally distinct color so user observation is unambiguous.
    variants = [
        # First: try cmd=0x02 with LED=0x00 (the obvious analogue of the brightness fix)
        ("V1 cmd=0x02 LED=0x00 [VARSTORE,0,1,0,0,1,RGB]", 0xFF, 0x00, 0x00,
         0x02, lambda r,g,b: [0x01, 0x00, 0x01, 0x00, 0x00, 0x01, r, g, b]),

        # cmd=0x05 STATIC with VARSTORE+LED=0x00 (some Razer fw use cmd=0x05)
        ("V2 cmd=0x05 [VARSTORE,0,RGB]", 0x00, 0xFF, 0x00,
         0x05, lambda r,g,b: [0x01, 0x00, r, g, b]),

        # cmd=0x05 with VARSTORE+RGB only
        ("V3 cmd=0x05 [VARSTORE,RGB]", 0x00, 0x00, 0xFF,
         0x05, lambda r,g,b: [0x01, r, g, b]),

        # cmd=0x02 with effect-id then RGB (set_effect_static + color)
        ("V4 cmd=0x02 [VARSTORE,0,0x01,RGB]", 0xFF, 0xFF, 0x00,
         0x02, lambda r,g,b: [0x01, 0x00, 0x01, r, g, b]),

        # Pure RGB at args[0..2] under cmd=0x02
        ("V5 cmd=0x02 [RGB only]", 0xFF, 0x00, 0xFF,
         0x02, lambda r,g,b: [r, g, b]),

        # cmd=0x03 with RGB at args[5..7] (matches Synapse animation slot pattern)
        ("V6 cmd=0x03 [0,0,0,0,0,RGB]", 0x00, 0xFF, 0xFF,
         0x03, lambda r,g,b: [0, 0, 0, 0, 0, r, g, b]),
    ]

    print(f"\nTrying {len(variants)} variants. Final color shows the winning variant.\n")
    time.sleep(2.0)

    for i, (label, r, g, b, cmd, factory) in enumerate(variants):
        tid = 0x82 + i
        args = factory(r, g, b)
        pkt = build_pkt(tid, 0x0F, cmd, args)
        wrote = h.send_feature_report(b'\x00' + pkt)
        print(f"  [{i}] {label} -> RGB=({r:#04x},{g:#04x},{b:#04x})  args={args}  wrote={wrote}", flush=True)
        time.sleep(2.5)

    h.close()
    print("\n[Final brightness 100%. What color is Joro showing?]")


if __name__ == "__main__":
    sys.exit(main() or 0)
