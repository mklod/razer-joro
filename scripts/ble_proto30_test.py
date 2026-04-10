# Last modified: 2026-04-10--1324
# Protocol30 BLE Test — verified commands + try lighting & keymaps
#
# Confirmed format: [txnId, 0, 0, 0, USB_CLASS, USB_CMD, 0, 0] for GET
# With data: [txnId, 0, len_hi, len_lo, USB_CLASS, USB_CMD, sub1, sub2, data...]

import asyncio
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


def cmd(cls, cid, data=None, sub1=0, sub2=0):
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    if data:
        dlen = len(data)
        hdr = bytes([txn_id, 0, (dlen >> 8) & 0xFF, dlen & 0xFF, cls, cid, sub1, sub2])
        return hdr + bytes(data)
    else:
        return bytes([txn_id, 0, 0, 0, cls, cid, sub1, sub2])


async def tx(client, pkt, label, wait=0.5):
    responses.clear()
    print(f"\n>>> {label}  TX[{len(pkt)}]: {hx(pkt)}")
    await client.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(wait)
    return list(responses)


async def test():
    print(f"Connecting to {PROXY_ADDR}...")
    dev = await BleakScanner.find_device_by_address(PROXY_ADDR, timeout=10)
    if not dev:
        print("Not found"); return

    async with BleakClient(dev) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(0.3)

        # === VERIFIED COMMANDS ===
        print("\n========== VERIFIED ==========")
        await tx(c, cmd(0x00, 0x81), "GET firmware (0x00/0x81)")
        await tx(c, cmd(0x07, 0x80), "GET battery (0x07/0x80)")

        # === SCAN ALL USB-KNOWN COMMANDS OVER BLE ===
        print("\n========== SCAN USB COMMANDS ==========")

        # Class 0x00: device info
        await tx(c, cmd(0x00, 0x82), "GET serial? (0x00/0x82)")
        await tx(c, cmd(0x00, 0x83), "GET device type? (0x00/0x83)")
        await tx(c, cmd(0x00, 0x87), "GET ?(0x00/0x87)")

        # Class 0x02: keymap
        await tx(c, cmd(0x02, 0x8F), "GET keymap (0x02/0x8F)")
        await tx(c, cmd(0x02, 0x82), "GET keymap? (0x02/0x82)")
        await tx(c, cmd(0x02, 0x83), "GET keymap? (0x02/0x83)")

        # Class 0x06: idle/sleep
        await tx(c, cmd(0x06, 0x86), "GET idle config (0x06/0x86)")
        await tx(c, cmd(0x06, 0x8E), "GET idle ext (0x06/0x8E)")

        # Class 0x07: battery/power
        await tx(c, cmd(0x07, 0x82), "GET power? (0x07/0x82)")

        # Class 0x0F: lighting
        await tx(c, cmd(0x0F, 0x84), "GET brightness (0x0F/0x84)")
        await tx(c, cmd(0x0F, 0x82), "GET effect? (0x0F/0x82)")

        # === TRY SET STATIC COLOR (RED) ===
        print("\n========== SET LIGHTING ==========")
        # USB format: class=0x0F, cmd=0x02, args=[01,05,01,00,00,01,FF,00,00]
        color_args = [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]
        await tx(c, cmd(0x0F, 0x02, color_args), "SET static RED (0x0F/0x02)")

        # === TRY SET BRIGHTNESS ===
        # USB format: class=0x0F, cmd=0x04, args=[01,05,brightness]
        await tx(c, cmd(0x0F, 0x04, [0x01, 0x05, 0xFF]), "SET brightness MAX (0x0F/0x04)")
        await tx(c, cmd(0x0F, 0x04, [0x01, 0x05, 0x40]), "SET brightness 25% (0x0F/0x04)")

        # === GET BRIGHTNESS AFTER SET ===
        # USB format: class=0x0F, cmd=0x84, args=[01]
        await tx(c, cmd(0x0F, 0x84, [0x01]), "GET brightness (0x0F/0x84 with arg)")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(test())
