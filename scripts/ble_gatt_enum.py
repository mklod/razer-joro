# Last modified: 2026-04-10--0210
"""Enumerate Joro BLE GATT services via WinRT."""

import asyncio

async def enumerate_gatt():
    from winrt.windows.devices.bluetooth import BluetoothLEDevice

    addr_int = 0xC8E2775D2FA2

    print("Connecting to Joro...")
    device = await BluetoothLEDevice.from_bluetooth_address_async(addr_int)
    print(f"Name: {device.name}")
    print(f"Connection: {device.connection_status}")

    result = await device.get_gatt_services_async()
    print(f"GATT status: {result.status}")

    for svc in result.services:
        print(f"\nService: {svc.uuid}")
        chars_result = await svc.get_characteristics_async()
        for ch in chars_result.characteristics:
            p = ch.characteristic_properties
            props = []
            if p & 0x01: props.append("broadcast")
            if p & 0x02: props.append("read")
            if p & 0x04: props.append("write-no-resp")
            if p & 0x08: props.append("write")
            if p & 0x10: props.append("notify")
            if p & 0x20: props.append("indicate")
            prop_str = ", ".join(props)
            print(f"  Char: {ch.uuid} [{prop_str}] handle={ch.attribute_handle}")

            # Try reading readable chars
            if p & 0x02:
                try:
                    read_result = await ch.read_value_async()
                    if read_result.status == 0:
                        data = bytes(read_result.value)
                        try:
                            text = data.decode("utf-8", errors="replace")
                            print(f"    Value: {data.hex()}  ({text})")
                        except:
                            print(f"    Value: {data.hex()}")
                except Exception as e:
                    print(f"    Read failed: {e}")

asyncio.run(enumerate_gatt())
