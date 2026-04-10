#!/usr/bin/env python3
# proto/explore_persistent.py
# Last modified: 2026-04-10--0215
"""
Explore class 0x02 commands to find persistent storage / save command.

Known class 0x02 commands:
  GET: 0x82, 0x83, 0x87(2b), 0x8D(5b), 0x8F(keymap), 0xA4, 0xA8
  SET: 0x02, 0x03, 0x07, 0x0D, 0x0F(keymap), 0x24(status=0x03), 0x28

0x0F is the known keymap SET. The others are unexplored.
Hypothesis: one of 0x02, 0x03, 0x07, 0x0D, 0x28 might be "save to persistent storage".

Strategy:
1. First, read all GET commands to see current state
2. Then try each SET with empty/minimal args and check response
"""

import sys
import time
sys.path.insert(0, '.')

from razer_packet import build_packet, format_packet, parse_packet
from usb_transport import UsbTransport

def try_get(transport, cmd_id, size=0, args=b""):
    """Send a GET command and print the response."""
    pkt = build_packet(0x02, cmd_id, size, args)
    try:
        resp = transport.send_packet(pkt)
        p = parse_packet(resp)
        print(f"  GET 0x02/0x{cmd_id:02X}: status=0x{p['status']:02X} size={p['data_size']}")
        if p['data_size'] > 0:
            args_hex = " ".join(f"{b:02X}" for b in p['args'])
            print(f"    args: {args_hex}")
        return p
    except Exception as e:
        print(f"  GET 0x02/0x{cmd_id:02X}: ERROR {e}")
        return None

def try_set(transport, cmd_id, size=0, args=b""):
    """Send a SET command and print the response."""
    pkt = build_packet(0x02, cmd_id, size, args)
    try:
        resp = transport.send_packet(pkt)
        p = parse_packet(resp)
        print(f"  SET 0x02/0x{cmd_id:02X}: status=0x{p['status']:02X} size={p['data_size']}")
        if p['data_size'] > 0:
            args_hex = " ".join(f"{b:02X}" for b in p['args'])
            print(f"    args: {args_hex}")
        return p
    except Exception as e:
        print(f"  SET 0x02/0x{cmd_id:02X}: ERROR {e}")
        return None

def main():
    with UsbTransport() as t:
        print("=== Reading all GET 0x02 commands ===")
        for cmd in [0x82, 0x83, 0x87, 0x8D, 0x8F, 0xA4, 0xA8]:
            try_get(t, cmd)
            time.sleep(0.05)

        print("\n=== Probing unexplored SET 0x02 commands (read-only, size=0) ===")
        print("(Sending with size=0 / empty args to see response without changing anything)")
        for cmd in [0x02, 0x03, 0x07, 0x0D, 0x24, 0x28]:
            try_set(t, cmd, size=0, args=b"")
            time.sleep(0.05)

        print("\n=== Checking openrazer source for clues ===")
        print("In openrazer, persistent storage commands often use:")
        print("  - Class 0x02 cmd 0x02: 'set keymap' variant or 'store profile'")
        print("  - VARSTORE byte in args: 0x00=volatile, 0x01=persistent")
        print()
        print("Let's try GET 0x8F (keymap) with different first args to check VARSTORE:")
        # Try reading keymap with varstore=0x00 vs 0x01
        for varstore in [0x00, 0x01]:
            p = try_get(t, 0x8F, size=1, args=bytes([varstore]))
            time.sleep(0.05)

if __name__ == "__main__":
    main()
