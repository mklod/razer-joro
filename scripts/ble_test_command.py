# Last modified: 2026-04-10--0215
"""Test sending Razer commands over BLE GATT."""

import asyncio
import sys
sys.path.insert(0, "L:/PROJECTS/razer-joro/proto")

from razer_packet import build_packet, parse_packet, format_packet

JORO_BLE_ADDR = 0xC8E2775D2FA2

# Razer custom service
RAZER_SVC    = "52401523-f97c-7f90-0e7f-6c6f4e36db1c"
RAZER_WRITE  = "52401524-f97c-7f90-0e7f-6c6f4e36db1c"  # write
RAZER_NOTIFY = "52401525-f97c-7f90-0e7f-6c6f4e36db1c"  # read, notify


async def main():
    from winrt.windows.devices.bluetooth import BluetoothLEDevice
    from winrt.windows.devices.bluetooth.genericattributeprofile import (
        GattCommunicationStatus,
        GattWriteOption,
    )
    from winrt.windows.storage.streams import DataWriter, DataReader

    print("Connecting to Joro BLE...")
    device = await BluetoothLEDevice.from_bluetooth_address_async(JORO_BLE_ADDR)
    if device is None:
        print("Device not found")
        return

    print(f"Connected: {device.name}")

    # Get the Razer custom service
    import uuid as _uuid
    svc_result = await device.get_gatt_services_for_uuid_async(
        _uuid.UUID(RAZER_SVC)
    )
    if svc_result.status != GattCommunicationStatus.SUCCESS:
        svc_result = await device.get_gatt_services_async()

    service = None
    for svc in svc_result.services:
        if str(svc.uuid) == RAZER_SVC:
            service = svc
            break

    if service is None:
        print("Razer service not found")
        return

    print(f"Found Razer service: {service.uuid}")

    # Get write and notify characteristics
    chars_result = await service.get_characteristics_async()
    write_char = None
    notify_char = None
    for ch in chars_result.characteristics:
        if str(ch.uuid) == RAZER_WRITE:
            write_char = ch
        elif str(ch.uuid) == RAZER_NOTIFY:
            notify_char = ch

    if not write_char:
        print("Write characteristic not found")
        return
    if not notify_char:
        print("Notify characteristic not found")
        return

    print(f"Write char: {write_char.uuid}")
    print(f"Notify char: {notify_char.uuid}")

    # Build a firmware query packet (same as USB)
    pkt = build_packet(0x00, 0x81, 0)
    print(f"\nSending firmware query ({len(pkt)} bytes):")
    print(f"  {pkt.hex()}")

    # Write the packet
    writer = DataWriter()
    writer.write_bytes(pkt)
    write_result = await write_char.write_value_with_result_async(
        writer.detach_buffer()
    )
    print(f"Write status: {write_result.status}")

    if write_result.status != GattCommunicationStatus.SUCCESS:
        print("Write failed!")
        return

    # Small delay then read response
    await asyncio.sleep(0.1)

    read_result = await notify_char.read_value_async()
    if read_result.status == GattCommunicationStatus.SUCCESS:
        data = bytes(read_result.value)
        print(f"\nResponse ({len(data)} bytes):")
        print(f"  {data.hex()}")
        if len(data) >= 90:
            print(f"\n{format_packet(data)}")
        else:
            print(f"  (short response, raw: {data.hex()})")
    else:
        print(f"Read failed: {read_result.status}")

    # Also try get_brightness
    print("\n--- Brightness query ---")
    pkt2 = build_packet(0x0F, 0x84, 1, bytes([0x01]))
    writer2 = DataWriter()
    writer2.write_bytes(pkt2)
    write_result2 = await write_char.write_value_with_result_async(
        writer2.detach_buffer()
    )
    print(f"Write status: {write_result2.status}")

    await asyncio.sleep(0.1)

    read_result2 = await notify_char.read_value_async()
    if read_result2.status == GattCommunicationStatus.SUCCESS:
        data2 = bytes(read_result2.value)
        print(f"Response ({len(data2)} bytes): {data2.hex()}")
        if len(data2) >= 90:
            print(format_packet(data2))


asyncio.run(main())
