# Last modified: 2026-04-10--1530
# Protocol30 device mode probe — test getDeviceMode/setDeviceMode init sequence
# Run through MITM proxy or direct BLE connection
#
# Hypothesis: setDeviceMode(0x03, 0x00) = "driver mode" must be sent before
# SET commands are accepted. This is the init handshake.
#
# New firmware (post-update): ALL commands return FAILURE (0x03) with ff suffix.
# We test whether deviceMode commands are the gate.

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
    s = STATUS.get(data[7] if len(data) > 7 else 0, f"?{data[7] if len(data) > 7 else '?'}")
    dlen = (data[1] << 16 | data[2] << 8 | data[3]) if len(data) > 3 else 0
    print(f"  <<< [{len(data)}B]: {hx(data)}  status={s}  data_len={dlen}")
    responses.append(data)


def cmd8(c1, c2, cid=0, sub=0):
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    return bytes([txn_id, 0, 0, 0, c1, c2, cid, sub])


def cmd_data(c1, c2, cid, sub, data):
    global txn_id
    txn_id = (txn_id + 1) & 0xFF
    dlen = len(data)
    hdr = bytes([txn_id, 0, (dlen >> 8) & 0xFF, dlen & 0xFF, c1, c2, cid, sub])
    return hdr + bytes(data)


async def tx(client, pkt, label, wait=0.5):
    responses.clear()
    print(f"\n>>> {label}")
    print(f"    TX[{len(pkt)}B]: {hx(pkt)}")
    await client.write_gatt_char(CHAR_TX, pkt, response=False)
    await asyncio.sleep(wait)
    # Collect multi-packet responses
    if responses and len(responses) > 0:
        r = responses[0]
        dlen = (r[1] << 16 | r[2] << 8 | r[3]) if len(r) > 3 else 0
        if dlen > 0 and len(responses) == 1:
            # Wait for continuation packet
            await asyncio.sleep(0.3)
    return list(responses)


async def probe():
    addr = sys.argv[1] if len(sys.argv) > 1 else PROXY_ADDR
    print(f"Connecting to {addr}...")
    dev = await BleakScanner.find_device_by_address(addr, timeout=10)
    if not dev:
        print("Not found — is the proxy advertising?")
        return

    async with BleakClient(dev) as c:
        print(f"Connected: {c.is_connected}")
        await c.start_notify(CHAR_RX, on_rx)
        await asyncio.sleep(0.5)  # Wait for unsolicited notification

        print("\n" + "="*60)
        print("PHASE 1: Test current state (expect FAILURE on new FW)")
        print("="*60)

        # GET firmware — baseline test
        await tx(c, cmd8(0x00, 0x81), "GET firmware (0x00/0x81)")

        # GET battery
        await tx(c, cmd8(0x07, 0x80), "GET battery (0x07/0x80)")

        print("\n" + "="*60)
        print("PHASE 2: Device mode commands")
        print("="*60)

        # GET device mode — byte 4=0x00, byte 5=0x84
        # From Synapse: getDeviceMode is called on connect
        await tx(c, cmd8(0x00, 0x84), "GET deviceMode (0x00/0x84)")

        # Also try with different byte positions
        await tx(c, cmd8(0x00, 0x00, 0x84, 0x00), "GET deviceMode alt (cmd=0x84)")

        print("\n" + "="*60)
        print("PHASE 3: SET device mode (driver mode = 0x03)")
        print("="*60)

        # SET device mode — class 0x00, cmd 0x04, data=[0x03, 0x00]
        # This puts the keyboard in "driver mode" where it accepts SET commands
        await tx(c, cmd_data(0x00, 0x04, 0x00, 0x00, [0x03, 0x00]),
                 "SET deviceMode=0x03 (0x00/0x04 data=[03,00])")

        # Alternative byte positions for SET device mode
        await tx(c, cmd_data(0x00, 0x00, 0x04, 0x00, [0x03, 0x00]),
                 "SET deviceMode alt1 (cmd=0x04 data=[03,00])")

        # Maybe the command ID for SET is 0x84 with different sub
        await tx(c, cmd_data(0x00, 0x04, 0x03, 0x00, []),
                 "SET deviceMode inline (0x00/0x04/0x03)")

        print("\n" + "="*60)
        print("PHASE 4: If any mode set worked, retry GETs")
        print("="*60)

        await tx(c, cmd8(0x00, 0x81), "GET firmware (retry after mode set)")
        await tx(c, cmd8(0x07, 0x80), "GET battery (retry after mode set)")

        print("\n" + "="*60)
        print("PHASE 5: Scan for auth-like commands")
        print("="*60)

        # Try various class/cmd combos that might be auth-related
        # Class 0x00 with various command IDs
        for cmd_id in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06,
                       0x80, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8A]:
            r = await tx(c, cmd8(0x00, cmd_id), f"class=0x00 cmd=0x{cmd_id:02x}", wait=0.25)

        # Class 0x01 — might have auth commands
        for cmd_id in [0x00, 0x01, 0x02, 0x03, 0x04, 0x80, 0x81, 0x82, 0x83, 0x84]:
            r = await tx(c, cmd8(0x01, cmd_id), f"class=0x01 cmd=0x{cmd_id:02x}", wait=0.25)

        # Class 0x10 — BLE-native command class mentioned in memory
        for cmd_id in [0x00, 0x01, 0x02, 0x03, 0x04, 0x05,
                       0x80, 0x81, 0x82, 0x83, 0x84, 0x85]:
            r = await tx(c, cmd8(0x10, cmd_id), f"class=0x10 cmd=0x{cmd_id:02x}", wait=0.25)

        print("\n" + "="*60)
        print("PHASE 6: Try sending the session bytes from old FW")
        print("="*60)

        # The old FW response had: 2a e5 10 14 67 a7 71 31 ed f5 60 d9
        # Maybe we need to echo these back as a session token?
        # Try class 0x00, cmd 0x00, with session data
        session_bytes = [0x2a, 0xe5, 0x10, 0x14, 0x67, 0xa7, 0x71, 0x31, 0xed, 0xf5, 0x60, 0xd9]
        await tx(c, cmd_data(0x00, 0x00, 0x00, 0x00, session_bytes),
                 "Echo old session bytes as auth token")

        print("\nDone. Check results above for any SUCCESS responses.")


if __name__ == "__main__":
    asyncio.run(probe())
