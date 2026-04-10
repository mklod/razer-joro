# Last modified: 2026-04-10--1324
# Quick BLE test — monitors serial for upstream connect, then immediately probes

import asyncio
import serial
import time
import sys
from bleak import BleakClient, BleakScanner

PROXY_ADDR = "CF:0D:6C:1C:2E:7D"
CHAR_TX = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"

STATUS = {1: "BUSY", 2: "SUCCESS", 3: "FAILURE", 4: "TIMEOUT",
          5: "NOT_SUPPORTED", 6: "PROFILE_NS", 7: "TARGET_NS"}

txn_id = 0
responses = []


def hx(data):
    return " ".join(f"{b:02x}" for b in data)


def on_rx(sender, data):
    responses.append(bytes(data))
    s = STATUS.get(data[7], f"?{data[7]}")
    print(f"  <<< [{len(data)}]: {hx(data)}  [{s}]")


def cmd(cls, cid, data=None):
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    if data:
        dlen = len(data)
        hdr = bytes([txn_id, 0, (dlen >> 8) & 0xFF, dlen & 0xFF, cls, cid, 0, 0])
        return hdr + bytes(data)
    else:
        return bytes([txn_id, 0, 0, 0, cls, cid, 0, 0])


async def tx(client, pkt, label, wait=0.4):
    responses.clear()
    print(f">>> {label}  TX[{len(pkt)}]: {hx(pkt)}")
    await client.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(wait)
    return list(responses)


def wait_for_upstream():
    """Wait for proxy serial to show upstream=connected."""
    print("Waiting for upstream connection on COM12...")
    for i in range(10):
        try:
            s = serial.Serial('COM12', 115200, timeout=1)
            break
        except:
            time.sleep(1)
    else:
        print("COM12 not found")
        return False

    start = time.time()
    buf = b''
    while time.time() - start < 60:
        chunk = s.read(1024)
        if chunk:
            buf += chunk
            if b'upstream=connected' in buf:
                s.close()
                print("UPSTREAM CONNECTED — starting probe NOW")
                return True
    s.close()
    print("Timed out")
    return False


async def test():
    if not wait_for_upstream():
        return

    print(f"\nConnecting to proxy {PROXY_ADDR}...")
    dev = await BleakScanner.find_device_by_address(PROXY_ADDR, timeout=5)
    if not dev:
        print("Proxy not found")
        return

    async with BleakClient(dev) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(0.2)

        print("\n--- Quick command burst ---")
        await tx(c, cmd(0x00, 0x81), "GET firmware", wait=0.3)
        await tx(c, cmd(0x07, 0x80), "GET battery", wait=0.3)
        await tx(c, cmd(0x00, 0x82), "GET serial", wait=0.3)
        await tx(c, cmd(0x00, 0x83), "GET device type", wait=0.3)
        await tx(c, cmd(0x02, 0x8F), "GET keymap", wait=0.3)
        await tx(c, cmd(0x06, 0x86), "GET idle", wait=0.3)
        await tx(c, cmd(0x07, 0x82), "GET power", wait=0.3)
        await tx(c, cmd(0x0F, 0x84), "GET brightness", wait=0.3)

        # SET commands
        print("\n--- SET commands ---")
        # SET static color RED
        await tx(c, cmd(0x0F, 0x02, [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]),
                 "SET static RED", wait=0.5)
        # SET brightness max
        await tx(c, cmd(0x0F, 0x04, [0x01, 0x05, 0xFF]),
                 "SET brightness MAX", wait=0.5)
        # GET brightness to verify
        await tx(c, cmd(0x0F, 0x84, [0x01]),
                 "GET brightness verify", wait=0.3)

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(test())
