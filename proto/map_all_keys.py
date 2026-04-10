# proto/map_all_keys.py
# Last modified: 2026-04-09--2100
"""
Map all keyboard matrix indices by remapping batches to unique keys.
Run one batch at a time, then press every physical key to identify which
index maps to which key.

Strategy: remap a batch of indices to sequential number/letter keys.
User presses each physical key and records what comes out.
Unplug/replug between batches to reset.

Known so far:
  idx  1 = Grave (`)
  idx  2 = 1
  idx  3 = 2
  idx  4 = 3
  idx  5 = 4
  idx  6 = 5
  idx  7 = 6
  idx  8 = 7
  idx 30 = CapsLock
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


# We'll remap each index to a unique HID usage so we can identify it.
# Approach: remap index N to Num1-Num0 + letters to create unique output.
# But simpler: just remap one batch at a time and have user type every key.

# Actually, smartest approach: remap ALL indices (1-80) so each produces
# a unique 2-character sequence. But that's complex.

# Simplest: remap batch to F1-F12 (easy to detect in a text editor via
# key event viewer). Or just use sequential letters.

# Let's do it in batches of 16, using letters A-P per batch.
# Batch 1: idx 9-19   (we know 1-8 already)
# Batch 2: idx 20-35  (we know 30=CapsLock, but map the rest)
# Batch 3: idx 36-51
# Batch 4: idx 52-67
# Batch 5: idx 68-83

LETTER_A = 0x04  # HID usage for A

batches = {
    1: (9, 24),    # idx 9-24
    2: (25, 40),   # idx 25-40
    3: (41, 56),   # idx 41-56
    4: (57, 72),   # idx 57-72
    5: (73, 88),   # idx 73-88
}

if len(sys.argv) < 2:
    print("Usage: python map_all_keys.py <batch_number>")
    print()
    print("Batches:")
    for b, (start, end) in batches.items():
        print(f"  {b}: indices {start}-{end}")
    print()
    print("Known mappings:")
    print("  idx  1=Grave  2=1  3=2  4=3  5=4  6=5  7=6  8=7")
    print("  idx 30=CapsLock")
    print()
    print("Steps:")
    print("  1. Unplug/replug keyboard (reset previous remaps)")
    print("  2. Run: python map_all_keys.py 1")
    print("  3. Open a key tester (e.g. keyboardtester.com)")
    print("  4. Press every physical key, note which letter A-P appears")
    print("  5. Record results, then repeat for next batch")
    sys.exit(0)

batch = int(sys.argv[1])
if batch not in batches:
    print(f"Invalid batch {batch}. Use 1-5.")
    sys.exit(1)

start_idx, end_idx = batches[batch]

print(f"=== Batch {batch}: Remapping indices {start_idx}-{end_idx} ===")
print()

for idx in range(start_idx, end_idx + 1):
    offset = idx - start_idx
    letter_usage = LETTER_A + offset
    letter = chr(ord('A') + offset)
    entry = bytes([idx, 0x02, 0x02, 0x00, letter_usage, 0x00, 0x00, 0x00])
    send_only(0x02, 0x0F, 18, HEADER + entry)
    time.sleep(0.05)
    print(f"  idx {idx:2d} -> {letter} (usage 0x{letter_usage:02X})")

print()
print("Mapping reference:")
for idx in range(start_idx, end_idx + 1):
    offset = idx - start_idx
    letter = chr(ord('A') + offset)
    print(f"  '{letter}' = index {idx}")
print()
print("Now press every physical key. Note which letter each key produces.")
print("Keys that still produce their normal output are NOT in this batch.")
print("(Unplug/replug to reset when done)")
