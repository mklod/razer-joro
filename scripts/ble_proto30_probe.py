# Last modified: 2026-04-10--0440
# Protocol30 BLE Probe — properly formatted commands
#
# Protocol30 packet format (from Synapse source):
#   Byte 0: transaction ID (auto-increment)
#   Bytes 1-3: data length (24-bit LE: [0, hi, lo] for small lengths)
#   Byte 4: command class byte 1 (e[1])
#   Byte 5: command class byte 2 (e[2])
#   Byte 6: command ID
#   Byte 7: sub-command ID
#   Bytes 8+: data payload
#
# Response format:
#   Byte 0: echoed transaction ID
#   Bytes 1-3: response data length
#   Byte 4-5: command class echoed
#   Byte 6: command ID echoed
#   Byte 7: status (1=busy, 2=success, 3=failure, 4=timeout, 5=not supported)
#   Bytes 8+: response data

import asyncio
from bleak import BleakClient, BleakScanner

PROXY_ADDR = "CF:0D:6C:1C:2E:7D"
CHAR_TX = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"
CHAR_RX2 = "52401526-f97c-7f90-0e7f-6c6f4e36db1c"

STATUS_NAMES = {1: "BUSY", 2: "SUCCESS", 3: "FAILURE", 4: "TIMEOUT", 5: "NOT_SUPPORTED",
                6: "PROFILE_NOT_SUPPORTED", 7: "TARGET_NOT_SUPPORTED"}

txn_id = 0
last_rx = None


def hex_str(data):
    return " ".join(f"{b:02x}" for b in data)


def on_1525(sender, data):
    global last_rx
    last_rx = bytes(data)
    status = STATUS_NAMES.get(data[7], f"UNKNOWN({data[7]})")
    print(f"  <<< RX [{len(data)}]: {hex_str(data)}  status={status}")


def on_1526(sender, data):
    print(f"  <<< RX2 [{len(data)}]: {hex_str(data)}")


def make_cmd(class1, class2, cmd_id, sub_cmd=0, data=None):
    """Build a Protocol30 packet."""
    global txn_id
    txn_id = (txn_id + 1) & 0xFF

    payload = data or []
    data_len = len(payload)

    # 8-byte header + payload
    pkt = bytearray(8 + data_len)
    pkt[0] = txn_id
    # Bytes 1-3: data length (24-bit)
    pkt[1] = 0
    pkt[2] = (data_len >> 8) & 0xFF
    pkt[3] = data_len & 0xFF
    pkt[4] = class1
    pkt[5] = class2
    pkt[6] = cmd_id
    pkt[7] = sub_cmd
    for i, b in enumerate(payload):
        pkt[8 + i] = b

    # Pad to 20 bytes if shorter (BLE MTU)
    if len(pkt) < 20:
        pkt.extend(b'\x00' * (20 - len(pkt)))

    return bytes(pkt)


async def send_and_wait(client, pkt, label, wait=0.5):
    global last_rx
    last_rx = None
    print(f"\n>>> {label}")
    print(f"    TX [{len(pkt)}]: {hex_str(pkt)}")
    try:
        await client.write_gatt_char(CHAR_TX, pkt, response=False)
    except Exception as e:
        print(f"    WRITE FAILED: {e}")
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

        # Known USB commands — try them in Protocol30 format
        # USB uses: class=0x00, cmd=0x81 for get firmware
        # Protocol30: class is split into class1 (byte 4) and class2 (byte 5)

        print("\n=== GET FIRMWARE (class=0x00, id=0x81) ===")
        await send_and_wait(client, make_cmd(0x00, 0x00, 0x81), "get_firmware")

        # Try with class in byte 5 instead
        await send_and_wait(client, make_cmd(0x00, 0x00, 0x81, 0x00), "get_firmware v2")

        print("\n=== GET BRIGHTNESS (class=0x0F, id=0x84) ===")
        await send_and_wait(client, make_cmd(0x0F, 0x00, 0x84, 0x00, [0x01]), "get_brightness")
        await send_and_wait(client, make_cmd(0x00, 0x0F, 0x84, 0x00, [0x01]), "get_brightness v2")

        print("\n=== GET BATTERY (class=0x07, id=0x80) ===")
        # Battery is a common BLE command
        await send_and_wait(client, make_cmd(0x07, 0x00, 0x80), "get_battery")
        await send_and_wait(client, make_cmd(0x00, 0x07, 0x80), "get_battery v2")

        print("\n=== SCAN class bytes — try every class with cmd 0x80 (GET) ===")
        for c in [0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0A, 0x0F]:
            await send_and_wait(client, make_cmd(c, 0x00, 0x80, 0x00),
                                f"class=0x{c:02x} cmd=0x80", wait=0.3)

        print("\n=== Try Protocol30 header only (8 bytes, no data) ===")
        pkt = make_cmd(0x00, 0x00, 0x81)[:8]
        await send_and_wait(client, pkt, "header_only_8bytes")

        print("\n=== Get device mode (from Synapse source) ===")
        # getDeviceMode and setDeviceMode are called on connect
        await send_and_wait(client, make_cmd(0x00, 0x00, 0x84), "getDeviceMode guess1")
        await send_and_wait(client, make_cmd(0x00, 0x00, 0x02), "setDeviceMode guess1")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(probe())
