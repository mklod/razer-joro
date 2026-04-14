# Last modified: 2026-04-10--2100
# Direct BLE control of Razer Joro keyboard (no MITM proxy)
#
# Uses Protocol30 split writes: 8-byte header + data as separate GATT writes.
# Validates the same protocol the Rust btleplug module implements.
#
# Usage:
#   python ble_direct_control.py              # scan + connect + test
#   python ble_direct_control.py --addr C8:E2:77:5D:2F:9F   # connect by address
#   python ble_direct_control.py --scan       # just scan for BLE devices
#   python ble_direct_control.py --color FF0000   # set static color (hex RGB)
#   python ble_direct_control.py --brightness 128 # set brightness (0-255)

import asyncio
import argparse
import sys
from bleak import BleakClient, BleakScanner

# Known Joro BLE address (update if your keyboard differs)
JORO_ADDR = "C8:E2:77:5D:2F:9F"

CHAR_TX = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"
RAZER_SVC = "52401523-f97c-7f90-0e7f-6c6f4e36db1c"

STATUS_MAP = {1: "BUSY", 2: "SUCCESS", 3: "FAILURE", 4: "TIMEOUT",
              5: "NOT_SUPPORTED", 6: "PROFILE_NS", 7: "TARGET_NS"}


def hx(data):
    return " ".join(f"{b:02x}" for b in data)


class JoroBLE:
    def __init__(self, client):
        self.client = client
        self.txn_id = 0
        self.responses = asyncio.Queue()

    def _on_notify(self, sender, data):
        self.responses.put_nowait(bytes(data))
        status = STATUS_MAP.get(data[7], f"?0x{data[7]:02x}") if len(data) > 7 else "?"
        print(f"  <<< [{len(data)}B] {hx(data)}  [{status}]")

    async def setup(self):
        await self.client.start_notify(CHAR_RX, self._on_notify)
        # Drain unsolicited notifications on connect
        await asyncio.sleep(0.5)
        while not self.responses.empty():
            self.responses.get_nowait()

    def _next_txn(self):
        self.txn_id = (self.txn_id + 1) & 0xFF
        return self.txn_id

    async def _wait_response(self, timeout=2.0):
        """Wait for a notification response."""
        try:
            return await asyncio.wait_for(self.responses.get(), timeout)
        except asyncio.TimeoutError:
            return None

    async def send_get(self, cls, cmd, sub1=0, sub2=0, label=""):
        """Send a GET command (single 8-byte write)."""
        txn = self._next_txn()
        header = bytes([txn, 0, 0, 0, cls, cmd, sub1, sub2])
        # Drain old notifications
        while not self.responses.empty():
            self.responses.get_nowait()

        print(f"\n>>> GET {label} [{hx(header)}]")
        await self.client.write_gatt_char(CHAR_TX, header, response=True)

        # Wait for header response
        resp = await self._wait_response()
        if resp is None:
            print("  !!! TIMEOUT waiting for response")
            return None

        if len(resp) < 8:
            print(f"  !!! Response too short: {len(resp)}B")
            return None

        status = resp[7]
        if status != 2:
            print(f"  !!! Status: {STATUS_MAP.get(status, f'0x{status:02x}')}")
            return None

        data_len = resp[1]
        if data_len == 0:
            return resp

        # Wait for data continuation
        data_resp = await self._wait_response()
        if data_resp is None:
            print("  !!! TIMEOUT waiting for data continuation")
            return resp
        return data_resp[:data_len]

    async def send_set(self, cls, cmd, sub1, sub2, data, label=""):
        """Send a SET command (split write: header then data as separate writes)."""
        txn = self._next_txn()
        dlen = len(data)
        header = bytes([txn, dlen, 0, 0, cls, cmd, sub1, sub2])

        while not self.responses.empty():
            self.responses.get_nowait()

        print(f"\n>>> SET {label}")
        print(f"    header: [{hx(header)}]")
        print(f"    data:   [{hx(data)}]")

        # Split write: header first
        await self.client.write_gatt_char(CHAR_TX, header, response=True)
        await asyncio.sleep(0.15)
        # Then data as separate write
        await self.client.write_gatt_char(CHAR_TX, bytes(data), response=True)

        resp = await self._wait_response()
        if resp is None:
            print("  !!! TIMEOUT waiting for SET response")
            return False

        status = resp[7] if len(resp) > 7 else 0xFF
        if status != 2:
            print(f"  !!! SET failed: {STATUS_MAP.get(status, f'0x{status:02x}')}")
            return False

        print("  OK")
        return True

    # ── High-level commands ──────────────────────────────────────────────

    async def get_firmware(self):
        data = await self.send_get(0x00, 0x81, label="firmware")
        if data and len(data) >= 4:
            print(f"  Firmware: v{data[0]}.{data[1]}.{data[2]}.{data[3]}")
            return data
        return None

    async def get_battery(self):
        data = await self.send_get(0x07, 0x80, label="battery")
        if data:
            print(f"  Battery data: {hx(data)}")
        return data

    async def get_brightness(self):
        data = await self.send_get(0x10, 0x85, sub1=0x01, label="brightness")
        if data and len(data) >= 1:
            pct = round(data[0] / 255 * 100)
            print(f"  Brightness: {data[0]} ({pct}%)")
            return data[0]
        return None

    async def set_brightness(self, level):
        return await self.send_set(0x10, 0x05, 0x01, 0x00, [level],
                                   label=f"brightness={level}")

    async def set_static_color(self, r, g, b):
        return await self.send_set(0x10, 0x03, 0x01, 0x00,
                                   [0x01, 0x00, 0x00, 0x01, r, g, b],
                                   label=f"static color=({r},{g},{b})")

    async def set_breathing_single(self, r, g, b):
        return await self.send_set(0x10, 0x03, 0x01, 0x00,
                                   [0x02, 0x01, 0x00, 0x01, r, g, b],
                                   label=f"breathing=({r},{g},{b})")

    async def set_spectrum(self):
        return await self.send_set(0x10, 0x03, 0x01, 0x00,
                                   [0x03, 0x00, 0x00, 0x00],
                                   label="spectrum cycling")


async def scan_devices():
    """Scan for BLE devices and list them."""
    print("Scanning for BLE devices (10s)...")
    discovered = {}

    def detection_cb(device, adv_data):
        discovered[device.address] = (device, adv_data)

    scanner = BleakScanner(detection_callback=detection_cb)
    await scanner.start()
    await asyncio.sleep(10)
    await scanner.stop()

    print(f"\nFound {len(discovered)} devices:")
    for addr, (dev, adv) in sorted(discovered.items(),
                                    key=lambda x: x[1][1].rssi or -999,
                                    reverse=True):
        name = adv.local_name or dev.name or "(unnamed)"
        rssi = adv.rssi or "?"
        print(f"  {addr}  rssi={rssi:>4}  {name}")
        if adv.service_uuids:
            for u in adv.service_uuids:
                print(f"    svc: {u}")
    return discovered


async def connect_and_test(addr, args):
    """Connect to keyboard and run test sequence or apply settings."""
    print(f"Connecting to {addr}...")

    # Try direct connect by address (works for paired devices on Windows)
    client = BleakClient(addr, timeout=15)
    try:
        await client.connect()
    except Exception as e:
        print(f"Direct connect failed: {e}")
        print("Trying scan-based discovery...")
        dev = await BleakScanner.find_device_by_address(addr, timeout=10)
        if not dev:
            print(f"Device {addr} not found in scan.")
            print("Hint: Try --scan to see available devices, or put keyboard in pairing mode.")
            return
        client = BleakClient(dev, timeout=15)
        await client.connect()

    print(f"Connected: {client.is_connected}")

    # List services
    print("\nGATT services:")
    for svc in client.services:
        print(f"  {svc.uuid}  ({svc.description or '?'})")
        for c in svc.characteristics:
            props = ", ".join(c.properties)
            print(f"    {c.uuid}  [{props}]")

    joro = JoroBLE(client)
    await joro.setup()

    try:
        if args.scan_only:
            return

        # If specific command requested
        if args.color:
            hex_color = args.color.lstrip('#')
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            await joro.set_static_color(r, g, b)
            return

        if args.brightness is not None:
            await joro.set_brightness(args.brightness)
            return

        if args.spectrum:
            await joro.set_spectrum()
            return

        if args.breathing:
            hex_color = args.breathing.lstrip('#')
            r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
            await joro.set_breathing_single(r, g, b)
            return

        # Default: full test sequence
        print("\n" + "=" * 60)
        print("JORO BLE DIRECT CONTROL TEST")
        print("=" * 60)

        await joro.get_firmware()
        await joro.get_battery()
        await joro.get_brightness()

        print("\n--- SET brightness 50% ---")
        await joro.set_brightness(128)
        await asyncio.sleep(0.5)
        await joro.get_brightness()

        print("\n--- SET static RED ---")
        await joro.set_static_color(0xFF, 0x00, 0x00)
        await asyncio.sleep(1)

        print("\n--- SET static GREEN ---")
        await joro.set_static_color(0x00, 0xFF, 0x00)
        await asyncio.sleep(1)

        print("\n--- SET static BLUE ---")
        await joro.set_static_color(0x00, 0x00, 0xFF)
        await asyncio.sleep(1)

        print("\n--- SET spectrum cycling ---")
        await joro.set_spectrum()

        print("\n--- Restore brightness 100% ---")
        await joro.set_brightness(0xFF)

        print("\n" + "=" * 60)
        print("TEST COMPLETE")
        print("=" * 60)

    finally:
        await client.disconnect()
        print("\nDisconnected.")


def main():
    parser = argparse.ArgumentParser(description="Direct BLE control for Razer Joro")
    parser.add_argument("--addr", default=JORO_ADDR,
                        help=f"BLE address (default: {JORO_ADDR})")
    parser.add_argument("--scan", dest="scan_only", action="store_true",
                        help="Just scan for BLE devices")
    parser.add_argument("--color", help="Set static color (hex, e.g. FF0000)")
    parser.add_argument("--brightness", type=int, help="Set brightness (0-255)")
    parser.add_argument("--spectrum", action="store_true", help="Set spectrum cycling")
    parser.add_argument("--breathing", help="Set breathing effect (hex color)")
    args = parser.parse_args()

    if args.scan_only:
        asyncio.run(scan_devices())
    else:
        asyncio.run(connect_and_test(args.addr, args))


if __name__ == "__main__":
    main()
