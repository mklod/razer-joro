# proto/enumerate.py
# Last modified: 2026-04-09
"""
Enumerate all Razer HID devices.

Lists every HID interface for VID 0x1532 — helps identify:
- Joro wired PID (0x02CD)
- 2.4GHz dongle PID (unknown)
- Which interface index to use for control transfers
"""

import hid

RAZER_VID = 0x1532
JORO_PID = 0x02CD


def enumerate_razer():
    """List all Razer HID devices with full interface details."""
    devices = hid.enumerate(RAZER_VID, 0)
    if not devices:
        print("No Razer HID devices found.")
        return []

    print(f"Found {len(devices)} Razer HID interface(s):\n")
    for d in devices:
        print(f"  Product:   {d['product_string']}")
        print(f"  VID/PID:   {d['vendor_id']:04X}:{d['product_id']:04X}")
        print(f"  Path:      {d['path']}")
        print(f"  Interface: {d['interface_number']}")
        print(f"  Usage:     page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X}")
        print()
    return devices


def find_joro_control_interface():
    """Find the Joro control interface (usage_page 0x0001, usage 0x0006 — keyboard).

    The control interface for Razer command packets is typically interface 0
    with usage_page 0x0001. If multiple interfaces exist, we need the one
    that accepts feature reports (report_id 0x00, 90 bytes).
    """
    devices = hid.enumerate(RAZER_VID, JORO_PID)
    if not devices:
        print("Joro not found. Is it connected via USB?")
        return None

    print(f"Joro interfaces ({len(devices)}):\n")
    for d in devices:
        print(f"  iface={d['interface_number']} usage_page=0x{d['usage_page']:04X} "
              f"usage=0x{d['usage']:04X} path={d['path']}")

    # Try interface 0 first, fall back to iterating
    for d in devices:
        if d["interface_number"] == 0:
            print(f"\nRecommended control interface: iface=0, path={d['path']}")
            return d
    # If no iface 0, return first
    print(f"\nNo iface 0 found, using first: path={devices[0]['path']}")
    return devices[0]


if __name__ == "__main__":
    print("=== All Razer devices ===\n")
    enumerate_razer()
    print("\n=== Joro control interface ===\n")
    find_joro_control_interface()
