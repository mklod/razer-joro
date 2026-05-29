#!/usr/bin/env python3
"""Probe Joro's fn/mm toggle over BLE Protocol30 directly.
Tests class=0x05 cmd=0x04 as the most likely 'set device mode' command.

Synapse must NOT be running (GATT sessions are exclusive).
"""
import asyncio, sys
from bleak import BleakClient, BleakScanner

JORO_ADDR = "C8:E2:77:5D:2F:A4"
CHAR_TX = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"

STATUS = {1:"BUSY", 2:"SUCCESS", 3:"FAILURE", 4:"TIMEOUT",
          5:"NOT_SUPPORTED", 6:"PROFILE_NS", 7:"TARGET_NS"}

txn = 0
def hx(b): return " ".join(f"{x:02x}" for x in b)

def cmd8(c1, c2, s1=0, s2=0):
    global txn
    txn = (txn + 1) & 0xFF
    return bytes([txn, 0, 0, 0, c1, c2, s1, s2])

def cmd_data(c1, c2, s1, s2, data):
    global txn
    txn = (txn + 1) & 0xFF
    dlen = len(data)
    return bytes([txn, 0, (dlen>>8)&0xFF, dlen&0xFF, c1, c2, s1, s2]) + bytes(data)

responses = []
def on_rx(sender, data):
    d = bytes(data)
    s = STATUS.get(d[7] if len(d)>7 else 0, f"?{d[7] if len(d)>7 else '?'}")
    print(f"  <<< [{len(d)}B] {hx(d)}  status={s}")
    responses.append(d)

async def split_set(c, header, data, label):
    """Joro SET commands need the header and payload as 2 separate writes."""
    responses.clear()
    print(f"\n>>> {label}")
    print(f"    hdr [{len(header)}B]: {hx(header)}")
    print(f"    dat [{len(data)}B]: {hx(data)}")
    await c.write_gatt_char(CHAR_TX, header, response=False)
    await asyncio.sleep(0.05)
    await c.write_gatt_char(CHAR_TX, data, response=False)
    await asyncio.sleep(0.5)
    return list(responses)

async def one(c, pkt, label):
    responses.clear()
    print(f"\n>>> {label}")
    print(f"    [{len(pkt)}B]: {hx(pkt)}")
    await c.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(0.5)
    return list(responses)

async def main():
    target_mode = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    print(f"Target mode: {target_mode} (3=driver/Fn, 0=normal/MM)")
    async with BleakClient(JORO_ADDR, timeout=15) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(0.3)
        # Baseline: GET device mode via known class=0x05 cmd=0x84
        await one(c, cmd8(0x05, 0x84), "GET class=0x05 cmd=0x84 (current mode)")
        # Try class=0x05 cmd=0x04 SET with data=[mode, 0]
        hdr = bytes([0xff, 0, 0, 2, 0x05, 0x04, 0x00, 0x01])
        payload = bytes([target_mode, 0x00])
        await split_set(c, hdr, payload, f"SET class=0x05 cmd=0x04 sub=00,01 data=[{target_mode:02x},00]")
        await asyncio.sleep(0.5)
        # Re-read
        await one(c, cmd8(0x05, 0x84), "GET class=0x05 cmd=0x84 (after set)")
        await c.stop_notify(CHAR_RX)
    print("\nDone.")

if __name__ == '__main__':
    asyncio.run(main())
