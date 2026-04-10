#!/usr/bin/env python3
# proto/test_persistent.py
# Last modified: 2026-04-10--0220
"""
Test persistent remap storage.

Plan:
1. Remap index 1 (grave/backtick) → F12 (HID 0x45) via SET 0x02/0x0F
2. Verify it works (press backtick, should produce F12)
3. Try SET 0x02/0x0F with varstore=0x01 prefix
4. Try each "save" candidate command
5. After each attempt, user replugs and we check if remap persists

Usage:
  python test_persistent.py read       # Read current keymap entries
  python test_persistent.py write      # Write backtick→F12 (volatile, normal)
  python test_persistent.py write_p    # Write backtick→F12 with varstore=0x01 prefix
  python test_persistent.py save CMD   # Call SET 0x02/CMD with size=0 (CMD = 02,03,07,0D,28)
  python test_persistent.py reset      # Reset backtick to default (HID 0x35)
"""

import sys
import time
sys.path.insert(0, '.')

from razer_packet import build_packet, parse_packet
from usb_transport import UsbTransport

IDX_GRAVE = 1
HID_GRAVE = 0x35  # backtick/grave default
HID_F12 = 0x45    # target for testing

def read_keymap(t):
    """Read and display first 8 keymap entries."""
    pkt = build_packet(0x02, 0x8F, 0)
    resp = t.send_packet(pkt)
    p = parse_packet(resp)
    print(f"Keymap GET: status=0x{p['status']:02X} size={p['data_size']}")
    args = p['args']
    # Skip 10-byte header, entries are 8 bytes each
    for i in range(8):
        off = 10 + i * 8
        if off + 8 <= len(args):
            entry = args[off:off+8]
            idx = entry[0]
            usage = entry[4]
            print(f"  [{idx:3d}] usage=0x{usage:02X} raw={' '.join(f'{b:02X}' for b in entry)}")

def write_volatile(t, idx, usage):
    """Write a keymap entry (volatile, normal method)."""
    args = bytes([idx, 0x02, 0x02, 0x00, usage, 0x00, 0x00, 0x00])
    pkt = build_packet(0x02, 0x0F, len(args), args)
    # Send-only (keymap SET has no GET response)
    t.device.ctrl_transfer(0x21, 0x09, 0x0300, 0x03, pkt)
    time.sleep(0.05)
    print(f"Wrote idx={idx} usage=0x{usage:02X} (volatile)")

def write_persistent(t, idx, usage):
    """Write a keymap entry with varstore=0x01 prefix byte."""
    # Try with an extra leading byte for varstore
    args = bytes([0x01, idx, 0x02, 0x02, 0x00, usage, 0x00, 0x00, 0x00])
    pkt = build_packet(0x02, 0x0F, len(args), args)
    t.device.ctrl_transfer(0x21, 0x09, 0x0300, 0x03, pkt)
    time.sleep(0.05)
    print(f"Wrote idx={idx} usage=0x{usage:02X} (with varstore=0x01 prefix)")

def try_save(t, cmd_id):
    """Send a SET 0x02/CMD_ID with size=0 as potential save command."""
    pkt = build_packet(0x02, cmd_id, 0)
    resp = t.send_packet(pkt)
    p = parse_packet(resp)
    print(f"SET 0x02/0x{cmd_id:02X}: status=0x{p['status']:02X}")

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return

    cmd = sys.argv[1]

    with UsbTransport() as t:
        if cmd == "read":
            read_keymap(t)
        elif cmd == "write":
            write_volatile(t, IDX_GRAVE, HID_F12)
            print("Now press backtick — should produce F12 (opens devtools in browser)")
        elif cmd == "write_p":
            write_persistent(t, IDX_GRAVE, HID_F12)
            print("Now press backtick — should produce F12")
            print("Replug USB and check if it persists")
        elif cmd == "save":
            if len(sys.argv) < 3:
                print("Usage: test_persistent.py save CMD (e.g., save 02)")
                return
            cmd_id = int(sys.argv[2], 16)
            try_save(t, cmd_id)
            print(f"Sent save command 0x{cmd_id:02X}. Replug USB and check persistence.")
        elif cmd == "reset":
            write_volatile(t, IDX_GRAVE, HID_GRAVE)
            print("Reset backtick to default (grave)")
        else:
            print(f"Unknown command: {cmd}")
            print(__doc__)

if __name__ == "__main__":
    main()
