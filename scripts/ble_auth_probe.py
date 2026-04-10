# Last modified: 2026-04-10--0427
# BLE Auth Protocol Probe
# Connects to the MITM proxy's custom GATT service and probes the
# keyboard's authentication handshake by sending commands and logging responses.
#
# The proxy relays everything to/from the real Joro keyboard.

import asyncio
import sys
from bleak import BleakClient, BleakScanner

# Proxy dongle address
PROXY_ADDR = "CF:0D:6C:1C:2E:7D"

# Razer custom service UUIDs
SVC_UUID  = "52401523-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_TX   = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"  # write (command input)
CHAR_RX   = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"  # notify (response)
CHAR_RX2  = "52401526-f97c-7f90-0e7f-6c6f4e36db1c"  # notify (secondary)


def hex_dump(label, data):
    hex_str = " ".join(f"{b:02x}" for b in data)
    print(f"{label} [{len(data)} bytes]: {hex_str}")


def on_notify_1525(sender, data):
    hex_dump("<<< RX 1525", data)


def on_notify_1526(sender, data):
    hex_dump("<<< RX 1526", data)


async def probe():
    print(f"Scanning for proxy at {PROXY_ADDR}...")
    device = await BleakScanner.find_device_by_address(PROXY_ADDR, timeout=10)
    if not device:
        print("Proxy not found. Is the dongle running?")
        return

    print(f"Found: {device.name} ({device.address})")

    async with BleakClient(device) as client:
        print(f"Connected: {client.is_connected}")

        # List services
        print("\n--- Services ---")
        for svc in client.services:
            print(f"  {svc.uuid}: {svc.description}")
            for char in svc.characteristics:
                props = ", ".join(char.properties)
                print(f"    {char.uuid} [{props}]")

        # Subscribe to notifications — do this fast before Windows disconnects
        print("\n--- Subscribing to notifications ---")
        await client.start_notify(CHAR_RX, on_notify_1525)
        print("  Subscribed to 1525 (response)")
        try:
            await client.start_notify(CHAR_RX2, on_notify_1526)
            print("  Subscribed to 1526 (secondary)")
        except Exception as e:
            print(f"  1526 subscribe failed (non-fatal): {e}")

        # Brief wait for unsolicited notifications
        await asyncio.sleep(0.5)

        # Probe: send various command bytes and observe responses
        print("\n--- Probing auth protocol ---")

        # The keyboard sent: 01 00 00 00 00 00 00 03 [12-byte nonce]
        # Byte 0 = 0x01 (message type?), byte 7 = 0x03 (status: not authenticated)
        # Try responding with different first bytes

        probes = [
            # Echo back what the keyboard sent (type 0x01)
            bytes([0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00]),
            # Type 0x00 — might be "hello" or init
            bytes([0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00]),
            # Type 0x02 — might be "auth request"
            bytes([0x02, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00]),
            # Type 0x04
            bytes([0x04, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00]),
            # Try the USB-style init: transaction_id=0x1F, class=0x00, cmd=0x81 (get firmware)
            # Truncated to 20 bytes
            bytes([0x00, 0x1F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x81,
                   0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                   0x00, 0x00, 0x00, 0x00]),
        ]

        for i, cmd in enumerate(probes):
            hex_dump(f"\n>>> TX probe {i}", cmd)
            try:
                await client.write_gatt_char(CHAR_TX, cmd, response=True)
                print("  Write OK")
            except Exception as e:
                print(f"  Write failed: {e}")
                # Try without response
                try:
                    await client.write_gatt_char(CHAR_TX, cmd, response=False)
                    print("  Write (no response) OK")
                except Exception as e2:
                    print(f"  Write (no response) also failed: {e2}")

            # Wait for response notification
            await asyncio.sleep(1)

        # Final wait
        print("\n--- Final 3s wait for any delayed responses ---")
        await asyncio.sleep(3)

        print("\n--- Done ---")


if __name__ == "__main__":
    asyncio.run(probe())
