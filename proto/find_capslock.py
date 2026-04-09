# proto/find_capslock.py
# Last modified: 2026-04-09--1630
"""Find CapsLock's index in the keymap by binary search remapping."""

import time
import usb.core
import usb.backend.libusb1

dll_path = r"C:\Users\mklod\AppData\Local\razer-joro-venv\Lib\site-packages\libusb\_platform\windows\x86_64\libusb-1.0.dll"
backend = usb.backend.libusb1.get_backend(find_library=lambda x: dll_path)
dev = usb.core.find(idVendor=0x1532, idProduct=0x02CD, backend=backend)

HEADER = bytes([0x00] * 10)


def send_only(cmd_class, cmd_id, data_size=0, args=b''):
    buf = bytearray(90)
    buf[0] = 0x00; buf[1] = 0x1F; buf[5] = data_size; buf[6] = cmd_class; buf[7] = cmd_id
    for i, b in enumerate(args[:80]):
        buf[8 + i] = b
    crc = 0
    for b in buf[2:88]:
        crc ^= b
    buf[88] = crc
    dev.ctrl_transfer(0x21, 0x09, 0x0300, 0x03, bytes(buf))


def recv():
    return bytes(dev.ctrl_transfer(0xA1, 0x01, 0x0300, 0x03, 90))


def remap_index(idx, target_usage):
    """Remap a keymap index to a target HID usage."""
    entry = bytes([idx, 0x02, 0x02, 0x00, target_usage, 0x00, 0x00, 0x00])
    send_only(0x02, 0x0F, 18, HEADER + entry)
    time.sleep(0.1)


def restore_index(idx, original_usage):
    """Restore a keymap index to its original usage."""
    entry = bytes([idx, 0x02, 0x02, 0x00, original_usage, 0x00, 0x00, 0x00])
    send_only(0x02, 0x0F, 18, HEADER + entry)
    time.sleep(0.1)


# Strategy: remap a batch of indices to a distinctive key,
# ask user to press CapsLock, narrow down
# Using Pause/Break (0x48) as target since it's easy to detect

# First, let's just remap ALL indices from 1 to 80 to see the valid range
# by remapping index N to "N" as a letter (just to see what's valid)
# Actually simpler: remap indices in batches and check CapsLock

# Batch approach: remap indices 1-20 to F12 (0x45), test CapsLock
# If CapsLock becomes F12, it's in range 1-20. Then narrow down.

print("=== Finding CapsLock index ===")
print()

# Known: indices 1-8 = top number row (backtick, 1-7)
# Joro has ~80 keys. Let's check groups of 10.
# We remap a group to a harmless but detectable key.

# Actually, the simplest approach: remap index N to "no key" (0x00)
# and see which physical key stops working.
# But that could be confusing.

# Better: print the first 8 default entries for reference
send_only(0x02, 0x8F, 0)
time.sleep(0.05)
resp = recv()
data = resp[8:8+resp[5]]
entries = data[10:]
print("Default keymap (first 8 entries):")
for i in range(0, len(entries), 8):
    e = entries[i:i+8]
    if len(e) >= 5:
        print(f"  index={e[0]:3d} usage=0x{e[4]:02X}")

# Now try reading with larger data_size to get more entries
print()
print("Trying to read more entries with larger data_size:")
for ds_in in [0, 10, 20, 40, 74, 80]:
    send_only(0x02, 0x8F, ds_in, bytes([0x00] * ds_in))
    time.sleep(0.02)
    resp = recv()
    ds = resp[5]
    print(f"  request_size={ds_in:2d} -> response_size={ds}")

# The response size is always 74 regardless of request.
# So the first page is always 8 entries (indices 1-8).
# The full keyboard has ~80 keys. There MUST be a way to read other pages.

# Let me check if the 0x02/0x82 metadata tells us something
# It returned [0A 00 0A 00]. Maybe 0x0A = 10 pages.
# And 0x02/0x83 returned [00 00 00 00] - maybe current page?

# Try setting current page via 0x02/0x03 then reading 0x02/0x8F
print()
print("Setting page via 0x02/0x03 then reading:")
for page in range(10):
    send_only(0x02, 0x03, 4, bytes([page, 0x00, 0x00, 0x00]))
    time.sleep(0.02)
    send_only(0x02, 0x8F, 0)
    time.sleep(0.02)
    resp = recv()
    ds = resp[5]
    data = resp[8:8+ds]
    if ds > 14:
        first_idx = data[10]
        first_usage = data[14]
        print(f"  page={page}: first_idx={first_idx} first_usage=0x{first_usage:02X}")
