"""
Replay the EXACT brightness packet shape Synapse used (captured via Frida),
but vary the brightness level so we can see if Joro responds.

Captured packet (after HID report ID 0x00):
    status=00 trans=0x9d remain=0000 proto=0 dsize=3 class=0x0F cmd=0x04
    args=[VARSTORE=0x01, LED_ID=0x00, level=0x9e]
    CRC valid

Key correction vs prior probes: LED_ID byte 1 of args is 0x00 for the
dongle, NOT 0x05 (which was BACKLIGHT_LED for direct-USB Joro).
"""
import hid, time, sys

VID = 0x1532
PID = 0x009C

def build_brightness(trans_id, level):
    pkt = bytearray(90)
    pkt[0x00] = 0x00
    pkt[0x01] = trans_id
    pkt[0x05] = 3
    pkt[0x06] = 0x0F
    pkt[0x07] = 0x04
    pkt[0x08] = 0x01   # VARSTORE
    pkt[0x09] = 0x00   # LED_ID (DONGLE form)
    pkt[0x0a] = level
    crc = 0
    for b in pkt[2:88]: crc ^= b
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
        print("MI_00 not found"); return 1
    path = d['path']
    print(f"Opening: {path.decode('latin-1') if isinstance(path, bytes) else path}\n")

    h = hid.device()
    h.open_path(path)

    # Sweep brightness levels with maximum visible contrast.
    # Slowed to 3s per step so the user can clearly observe.
    levels = [0xFF, 0x10, 0xFF, 0x10, 0xFF, 0x80]
    print("Watch Joro brightness — should pulse: BRIGHT/DIM/BRIGHT/DIM/BRIGHT, end at ~50%\n")
    time.sleep(3.0)
    for i, lvl in enumerate(levels):
        tid = 0x80 + i
        pkt = build_brightness(tid, lvl)
        wrote = h.send_feature_report(b'\x00' + pkt)
        pct = round(lvl / 255 * 100)
        print(f"  trans=0x{tid:02x} brightness=0x{lvl:02x} ({pct}%)  wrote={wrote}", flush=True)
        time.sleep(3.0)

    h.close()
    print("\nFinal brightness was 0x80 (50%). Did Joro brightness change visibly?")


if __name__ == "__main__":
    sys.exit(main() or 0)
