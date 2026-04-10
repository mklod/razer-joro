# Last modified: 2026-04-10--1230
# Protocol30 8-byte header probe — keyboard responds to 8-byte writes correctly
# 20-byte padded commands get FAILURE, 8-byte headers get NOT_SUPPORTED or other real status

import asyncio
from bleak import BleakClient, BleakScanner

PROXY_ADDR = "CF:0D:6C:1C:2E:7D"
CHAR_TX = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"

STATUS = {1: "BUSY", 2: "SUCCESS", 3: "FAILURE", 4: "TIMEOUT",
          5: "NOT_SUPPORTED", 6: "PROFILE_NS", 7: "TARGET_NS"}

txn_id = 0
last_rx = None


def hx(data):
    return " ".join(f"{b:02x}" for b in data)


def on_rx(sender, data):
    global last_rx
    last_rx = bytes(data)
    s = STATUS.get(data[7], f"?{data[7]}")
    print(f"  <<< [{len(data)}]: {hx(data)}  [{s}]")


def cmd8(c1, c2, cid, sub=0):
    """8-byte header only — no data payload."""
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    return bytes([txn_id, 0, 0, 0, c1, c2, cid, sub])


def cmd_with_data(c1, c2, cid, sub, data):
    """8-byte header + data payload, no padding."""
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    dlen = len(data)
    hdr = bytes([txn_id, 0, (dlen >> 8) & 0xFF, dlen & 0xFF, c1, c2, cid, sub])
    return hdr + bytes(data)


async def tx(client, pkt, label, wait=0.4):
    global last_rx
    last_rx = None
    print(f"\n>>> {label}  TX[{len(pkt)}]: {hx(pkt)}")
    await client.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(wait)
    return last_rx


async def probe():
    print(f"Connecting to {PROXY_ADDR}...")
    dev = await BleakScanner.find_device_by_address(PROXY_ADDR, timeout=10)
    if not dev:
        print("Not found"); return

    async with BleakClient(dev) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(0.3)

        # USB commands we know work — try in Protocol30 8-byte format
        # USB class mapping: USB byte 6=class, byte 7=cmd
        # Protocol30: byte 4=class1, byte 5=class2, byte 6=cmdId, byte 7=subCmdId

        print("\n=== 8-byte header scans ===")

        # Try GET firmware: USB class=0x00, cmd=0x81
        # Possible mappings:
        await tx(c, cmd8(0x00, 0x00, 0x81, 0x00), "fw: c1=00 c2=00 cmd=81 sub=00")
        await tx(c, cmd8(0x00, 0x81, 0x00, 0x00), "fw: c1=00 c2=81 cmd=00 sub=00")
        await tx(c, cmd8(0x00, 0x00, 0x00, 0x81), "fw: c1=00 c2=00 cmd=00 sub=81")

        # Try GET brightness: USB class=0x0F, cmd=0x84
        await tx(c, cmd8(0x0F, 0x84, 0x00, 0x00), "brt: c1=0F c2=84 cmd=00 sub=00")
        await tx(c, cmd8(0x0F, 0x00, 0x84, 0x00), "brt: c1=0F c2=00 cmd=84 sub=00")
        await tx(c, cmd8(0x00, 0x0F, 0x84, 0x00), "brt: c1=00 c2=0F cmd=84 sub=00")

        # Try GET battery: USB class=0x07, cmd=0x80
        await tx(c, cmd8(0x07, 0x80, 0x00, 0x00), "bat: c1=07 c2=80")
        await tx(c, cmd8(0x07, 0x00, 0x80, 0x00), "bat: c1=07 c2=00 cmd=80")

        # USB idle: class=0x06, cmd=0x86
        await tx(c, cmd8(0x06, 0x86, 0x00, 0x00), "idle: c1=06 c2=86")
        await tx(c, cmd8(0x06, 0x00, 0x86, 0x00), "idle: c1=06 c2=00 cmd=86")

        print("\n=== Brute-force byte 4 (class1) with zeros elsewhere ===")
        for b4 in range(0x10):
            await tx(c, cmd8(b4, 0x00, 0x00, 0x00), f"b4=0x{b4:02x}", wait=0.2)

        print("\n=== Brute-force byte 6 (cmdId) with class=0x00 ===")
        for b6 in [0x00, 0x01, 0x02, 0x03, 0x04, 0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x8F, 0xFF]:
            await tx(c, cmd8(0x00, 0x00, b6, 0x00), f"cmd=0x{b6:02x}", wait=0.2)

        print("\n=== With data: GET firmware USB-style (class=0x00 cmd=0x81 size=0) ===")
        # Maybe the keyboard wants the USB-style args format in the data section
        await tx(c, cmd_with_data(0x00, 0x00, 0x81, 0x00, []), "fw hdr-only")
        await tx(c, cmd_with_data(0x00, 0x81, 0x00, 0x00, [0x01, 0x05]), "fw+args?")

        # Try sending full USB-style packet as data (status, txn, remaining, proto, size, class, cmd, args...)
        usb_get_fw = [0x00, 0x1F, 0x00, 0x00, 0x00, 0x00, 0x00, 0x81, 0x00, 0x00, 0x00, 0x00]
        await tx(c, cmd_with_data(0x00, 0x00, 0x00, 0x00, usb_get_fw), "usb-in-data")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(probe())
