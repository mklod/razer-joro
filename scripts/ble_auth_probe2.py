# Last modified: 2026-04-10--0432
# BLE Auth Protocol Probe v2 — deeper probing of the challenge-response
#
# Known so far:
# - Keyboard response: [echo_b0, 00*6, 0x03, <12-byte nonce>]
# - Byte 0 is echoed, byte 7 = 0x03 = "not authenticated"
# - 12-byte nonce is constant per session

import asyncio
from bleak import BleakClient, BleakScanner

PROXY_ADDR = "CF:0D:6C:1C:2E:7D"
CHAR_TX  = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX  = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX2 = "52401526-f97c-7f90-0e7f-6c6f4e36db1c"

last_rx = None
last_rx2 = None


def hex_str(data):
    return " ".join(f"{b:02x}" for b in data)


def on_1525(sender, data):
    global last_rx
    last_rx = bytes(data)
    print(f"  <<< 1525 [{len(data)}]: {hex_str(data)}")


def on_1526(sender, data):
    global last_rx2
    last_rx2 = bytes(data)
    print(f"  <<< 1526 [{len(data)}]: {hex_str(data)}")


async def send_and_wait(client, data, label="", wait=0.5):
    global last_rx, last_rx2
    last_rx = None
    last_rx2 = None
    print(f"\n>>> {label} [{len(data)}]: {hex_str(data)}")
    try:
        await client.write_gatt_char(CHAR_TX, data, response=False)
    except Exception as e:
        print(f"  WRITE FAILED: {e}")
        return None
    await asyncio.sleep(wait)
    return last_rx


async def probe():
    print(f"Connecting to proxy {PROXY_ADDR}...")
    device = await BleakScanner.find_device_by_address(PROXY_ADDR, timeout=10)
    if not device:
        print("Not found")
        return

    async with BleakClient(device) as client:
        print(f"Connected: {client.is_connected}")
        await client.start_notify(CHAR_RX, on_1525)
        try:
            await client.start_notify(CHAR_RX2, on_1526)
        except:
            pass

        await asyncio.sleep(0.3)

        # 1) Test all byte 0 values 0x00-0x0F to find command types
        print("\n=== PHASE 1: Scan byte 0 (command type) ===")
        for b0 in range(0x10):
            cmd = bytes([b0] + [0]*19)
            resp = await send_and_wait(client, cmd, f"type=0x{b0:02x}")

        # 2) Test byte 1 values
        print("\n=== PHASE 2: Scan byte 1 ===")
        for b1 in [0x01, 0x02, 0x04, 0x08, 0x10, 0x1F, 0x3F, 0xFF]:
            cmd = bytes([0x01, b1] + [0]*18)
            resp = await send_and_wait(client, cmd, f"b1=0x{b1:02x}")

        # 3) Send the nonce back as a response
        print("\n=== PHASE 3: Echo nonce back ===")
        if last_rx and len(last_rx) >= 20:
            nonce = last_rx[8:20]
            print(f"  Nonce: {hex_str(nonce)}")

            # Try: [0x01, 0x00*7, nonce]
            cmd = bytes([0x01, 0, 0, 0, 0, 0, 0, 0]) + nonce
            await send_and_wait(client, cmd, "echo nonce (type=0x01)")

            # Try: [0x02, 0x00*7, nonce]
            cmd = bytes([0x02, 0, 0, 0, 0, 0, 0, 0]) + nonce
            await send_and_wait(client, cmd, "echo nonce (type=0x02)")

            # Try: [0x03, 0x00*7, nonce]
            cmd = bytes([0x03, 0, 0, 0, 0, 0, 0, 0]) + nonce
            await send_and_wait(client, cmd, "echo nonce (type=0x03)")

        # 4) Try longer byte patterns in positions 1-7
        print("\n=== PHASE 4: Vary bytes 1-7 ===")
        for pos in range(1, 8):
            cmd = bytearray([0x01] + [0]*19)
            cmd[pos] = 0x01
            await send_and_wait(client, cmd, f"byte[{pos}]=0x01")

        # 5) Try all 0xFF
        print("\n=== PHASE 5: Full 0xFF ===")
        await send_and_wait(client, bytes([0xFF]*20), "all 0xFF")

        # 6) Read 1526 value
        print("\n=== PHASE 6: Read characteristics ===")
        try:
            val = await client.read_gatt_char(CHAR_RX)
            print(f"  READ 1525: {hex_str(val)}")
        except Exception as e:
            print(f"  READ 1525 failed: {e}")
        try:
            val = await client.read_gatt_char(CHAR_RX2)
            print(f"  READ 1526: {hex_str(val)}")
        except Exception as e:
            print(f"  READ 1526 failed: {e}")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(probe())
