# proto/find_capslock_v2.py
# Last modified: 2026-04-09--2100
"""
Find CapsLock's matrix index by remapping indices 20-35 to unique letters.
After running, press CapsLock — the letter that appears reveals the index.

Mapping: idx 20->A, 21->B, 22->C, ..., 35->P
If CapsLock outputs 'c', that means idx=22 is CapsLock.

After identification, press Enter to restore defaults (unplug/replug also works).
"""

import time
import sys
import usb.core
import usb.backend.libusb1

dll_path = r"C:\Users\mklod\AppData\Local\razer-joro-venv\Lib\site-packages\libusb\_platform\windows\x86_64\libusb-1.0.dll"
backend = usb.backend.libusb1.get_backend(find_library=lambda x: dll_path)
dev = usb.core.find(idVendor=0x1532, idProduct=0x02CD, backend=backend)
if not dev:
    print("ERROR: Joro not found")
    sys.exit(1)

HEADER = bytes([0x00] * 10)


def send_only(cls, cmd, ds=0, args=b''):
    buf = bytearray(90)
    buf[1] = 0x1F
    buf[5] = ds
    buf[6] = cls
    buf[7] = cmd
    for i, b in enumerate(args[:80]):
        buf[8 + i] = b
    crc = 0
    for b in buf[2:88]:
        crc ^= b
    buf[88] = crc
    dev.ctrl_transfer(0x21, 0x09, 0x0300, 0x03, bytes(buf))


# HID usage IDs for letters A-P (0x04-0x13)
# idx 20 -> A (0x04), idx 21 -> B (0x05), ..., idx 35 -> P (0x13)
START_IDX = 20
END_IDX = 35
LETTER_A_USAGE = 0x04  # HID usage for 'A'

print("=== CapsLock Index Finder v2 ===")
print()
print(f"Remapping indices {START_IDX}-{END_IDX} to letters A-P...")
print()

for idx in range(START_IDX, END_IDX + 1):
    letter_usage = LETTER_A_USAGE + (idx - START_IDX)
    letter = chr(ord('A') + (idx - START_IDX))
    entry = bytes([idx, 0x02, 0x02, 0x00, letter_usage, 0x00, 0x00, 0x00])
    send_only(0x02, 0x0F, 18, HEADER + entry)
    time.sleep(0.05)
    print(f"  idx {idx:2d} -> {letter} (usage 0x{letter_usage:02X})")

print()
print("Done! Now press CapsLock and observe what letter appears.")
print()
print("Mapping reference:")
for idx in range(START_IDX, END_IDX + 1):
    letter = chr(ord('A') + (idx - START_IDX))
    print(f"  '{letter}' = index {idx}")
print()
print("If CapsLock doesn't produce A-P, it's NOT in range 20-35.")
print("(Unplug/replug keyboard to reset all remaps)")
