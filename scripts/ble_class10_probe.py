# Last modified: 2026-04-10--1545
# Focused probe of BLE-native class 0x10 commands
# Connects through MITM proxy (upstream already connected to keyboard)
# Now with FIXED relay (no 20-byte padding)

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
    data = bytes(data)
    responses.append(data)
    status = STATUS.get(data[7], f"?0x{data[7]:02x}") if len(data) > 7 else "?"
    dlen = data[1] if len(data) > 1 else 0
    print(f"  RX [{len(data)}B]: {hx(data)}  status={status} dlen={dlen}", flush=True)


def cmd8(c1, c2, sub1=0, sub2=0):
    """8-byte header only — no data payload."""
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    return bytes([txn_id, 0, 0, 0, c1, c2, sub1, sub2])


def cmd_data(c1, c2, sub1, sub2, data):
    """8-byte header + exact data payload, no padding."""
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    dlen = len(data)
    hdr = bytes([txn_id, 0, (dlen >> 8) & 0xFF, dlen & 0xFF, c1, c2, sub1, sub2])
    return hdr + bytes(data)


async def tx(client, pkt, label, wait=0.6):
    responses.clear()
    print(f"\n>>> {label}", flush=True)
    print(f"  TX [{len(pkt)}B]: {hx(pkt)}", flush=True)
    await client.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(wait)
    # Check for multi-packet (dlen > 0 means data follows)
    if responses and responses[0][1] > 0 and len(responses) < 2:
        await asyncio.sleep(0.4)
    return list(responses)


async def probe():
    addr = sys.argv[1] if len(sys.argv) > 1 else PROXY_ADDR
    print(f"Connecting to proxy at {addr}...")
    async with BleakClient(addr, timeout=15) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(1.0)  # Collect any unsolicited notifications

        if responses:
            print(f"\nUnsolicited notifications received: {len(responses)}")
        responses.clear()

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 1: Verify old commands work (class 0x00, 0x07)")
        print("="*60)

        await tx(c, cmd8(0x00, 0x81), "GET firmware (0x00/0x81)")
        await tx(c, cmd8(0x00, 0x83), "GET deviceType (0x00/0x83)")
        await tx(c, cmd8(0x07, 0x80), "GET battery (0x07/0x80)")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 2: Read all class 0x10 GETs (BLE-native)")
        print("="*60)

        await tx(c, cmd8(0x10, 0x00), "0x10/0x00 (worked: dlen=2)")
        await tx(c, cmd8(0x10, 0x06), "0x10/0x06 (worked: dlen=0)")
        await tx(c, cmd8(0x10, 0x81), "0x10/0x81 (worked: dlen=6) — getFirmware?")
        await tx(c, cmd8(0x10, 0x82), "0x10/0x82 (worked: dlen=4)")
        await tx(c, cmd8(0x10, 0x83), "0x10/0x83 (worked: dlen=3) — getDeviceType?")
        await tx(c, cmd8(0x10, 0x84), "0x10/0x84 (worked: dlen=10) — getLighting?")
        await tx(c, cmd8(0x10, 0x86), "0x10/0x86 (worked: dlen=1)")

        # Also try 0x85 (getBrightness from Synapse source)
        await tx(c, cmd8(0x10, 0x85), "0x10/0x85 — getBrightness?")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 3: Class 0x10 GETs with sub-params")
        print("="*60)

        # Maybe sub-params select the LED target (like BACKLIGHT=0x05)
        await tx(c, cmd8(0x10, 0x84, 0x01, 0x00), "0x10/0x84 sub=01,00")
        await tx(c, cmd8(0x10, 0x84, 0x05, 0x00), "0x10/0x84 sub=05,00 (backlight)")
        await tx(c, cmd8(0x10, 0x84, 0x00, 0x05), "0x10/0x84 sub=00,05")
        await tx(c, cmd8(0x10, 0x85, 0x01, 0x00), "0x10/0x85 sub=01,00")
        await tx(c, cmd8(0x10, 0x85, 0x05, 0x00), "0x10/0x85 sub=05,00 (backlight)")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 4: SET brightness — various formats")
        print("="*60)

        # Format A: cmd=0x05, 1 byte brightness in data
        await tx(c, cmd_data(0x10, 0x05, 0, 0, [0xFF]),
                 "SET brt 0x10/0x05 data=[FF]")

        # Format B: cmd=0x04 (USB SET brightness cmd), 1 byte
        await tx(c, cmd_data(0x10, 0x04, 0, 0, [0xFF]),
                 "SET brt 0x10/0x04 data=[FF]")

        # Format C: cmd=0x05, brightness in sub-params
        await tx(c, cmd8(0x10, 0x05, 0xFF, 0x00),
                 "SET brt 0x10/0x05 sub=FF,00 (inline)")

        # Format D: cmd=0x04, brightness in sub-params
        await tx(c, cmd8(0x10, 0x04, 0xFF, 0x00),
                 "SET brt 0x10/0x04 sub=FF,00 (inline)")

        # Format E: cmd=0x05, USB-style args [VARSTORE, LED_ID, brightness]
        await tx(c, cmd_data(0x10, 0x05, 0, 0, [0x01, 0x05, 0xFF]),
                 "SET brt 0x10/0x05 data=[01,05,FF]")

        # Format F: cmd=0x04, USB-style args
        await tx(c, cmd_data(0x10, 0x04, 0, 0, [0x01, 0x05, 0xFF]),
                 "SET brt 0x10/0x04 data=[01,05,FF]")

        # Format G: brightness in data_len field (len=FF, no actual data)
        pkt = bytes([txn_id + 1, 0, 0, 0xFF, 0x10, 0x05, 0, 0])
        await tx(c, pkt, "SET brt 0x10/0x05 len_field=FF")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 5: SET color — various formats")
        print("="*60)

        # USB-style SET static: VARSTORE=1, LED=5, effect=0, speed=0, dir=0, colors=1, R,G,B
        await tx(c, cmd_data(0x10, 0x02, 0, 0,
                             [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]),
                 "SET color RED 0x10/0x02 USB-style [01,05,01,00,00,01,FF,00,00]")

        # Simpler: just RGB
        await tx(c, cmd_data(0x10, 0x02, 0, 0, [0xFF, 0x00, 0x00]),
                 "SET color RED 0x10/0x02 data=[FF,00,00]")

        # Try cmd=0x03 (SET effect in USB protocol)
        await tx(c, cmd_data(0x10, 0x03, 0, 0,
                             [0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]),
                 "SET effect 0x10/0x03 USB-style")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 6: Class 0x05 working commands")
        print("="*60)

        await tx(c, cmd8(0x05, 0x80), "0x05/0x80 (worked: dlen=0)")
        await tx(c, cmd8(0x05, 0x81), "0x05/0x81 (worked: dlen=9)")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 7: Class 0x01 cmd 0x86")
        print("="*60)

        await tx(c, cmd8(0x01, 0x86), "0x01/0x86 (worked: dlen=3)")

        # ============================================================
        print("\n" + "="*60)
        print("PHASE 8: Extended class 0x10 cmd scan (0x07-0x20)")
        print("="*60)

        for cmd in range(0x07, 0x21):
            await tx(c, cmd8(0x10, cmd), f"0x10/0x{cmd:02x}", wait=0.35)

        for cmd in range(0x87, 0x91):
            await tx(c, cmd8(0x10, cmd), f"0x10/0x{cmd:02x}", wait=0.35)

        print("\n" + "="*60)
        print("PROBE COMPLETE")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(probe())
