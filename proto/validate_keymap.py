# proto/validate_keymap.py
# Last modified: 2026-04-09--2100
"""Read and validate full keymap from Joro keyboard."""

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

KEY_NAMES = {
    0x00: '(none)', 0x01: 'ErrRollOver', 0x02: 'POSTFail', 0x03: 'ErrUndef',
    0x04: 'A', 0x05: 'B', 0x06: 'C', 0x07: 'D', 0x08: 'E', 0x09: 'F',
    0x0A: 'G', 0x0B: 'H', 0x0C: 'I', 0x0D: 'J', 0x0E: 'K', 0x0F: 'L',
    0x10: 'M', 0x11: 'N', 0x12: 'O', 0x13: 'P', 0x14: 'Q', 0x15: 'R',
    0x16: 'S', 0x17: 'T', 0x18: 'U', 0x19: 'V', 0x1A: 'W', 0x1B: 'X',
    0x1C: 'Y', 0x1D: 'Z', 0x1E: '1', 0x1F: '2', 0x20: '3', 0x21: '4',
    0x22: '5', 0x23: '6', 0x24: '7', 0x25: '8', 0x26: '9', 0x27: '0',
    0x28: 'Enter', 0x29: 'Esc', 0x2A: 'Bksp', 0x2B: 'Tab', 0x2C: 'Space',
    0x2D: 'Minus', 0x2E: 'Equal', 0x2F: 'LBracket', 0x30: 'RBracket',
    0x31: 'Backslash', 0x33: 'Semicolon', 0x34: 'Quote', 0x35: 'Grave',
    0x36: 'Comma', 0x37: 'Period', 0x38: 'Slash', 0x39: 'CapsLock',
    0x3A: 'F1', 0x3B: 'F2', 0x3C: 'F3', 0x3D: 'F4', 0x3E: 'F5', 0x3F: 'F6',
    0x40: 'F7', 0x41: 'F8', 0x42: 'F9', 0x43: 'F10', 0x44: 'F11', 0x45: 'F12',
    0x46: 'PrtScr', 0x47: 'ScrLk', 0x48: 'Pause', 0x49: 'Ins', 0x4A: 'Home',
    0x4B: 'PgUp', 0x4C: 'Del', 0x4D: 'End', 0x4E: 'PgDn', 0x4F: 'Right',
    0x50: 'Left', 0x51: 'Down', 0x52: 'Up', 0x53: 'NumLk',
    0x54: 'NumSlash', 0x55: 'NumStar', 0x56: 'NumMinus', 0x57: 'NumPlus',
    0x58: 'NumEnter', 0x59: 'Num1', 0x5A: 'Num2', 0x5B: 'Num3',
    0x5C: 'Num4', 0x5D: 'Num5', 0x5E: 'Num6', 0x5F: 'Num7',
    0x60: 'Num8', 0x61: 'Num9', 0x62: 'Num0', 0x63: 'NumDot',
    0x65: 'App', 0x87: 'Intl1', 0x88: 'Intl2', 0x89: 'Intl3',
    0xE0: 'LCtrl', 0xE1: 'LShift', 0xE2: 'LAlt', 0xE3: 'LGui',
    0xE4: 'RCtrl', 0xE5: 'RShift', 0xE6: 'RAlt', 0xE7: 'RGui',
}


def send_recv(cls, cmd, ds=0, args=b''):
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
    time.sleep(0.02)
    return bytes(dev.ctrl_transfer(0xA1, 0x01, 0x0300, 0x03, 90))


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


def parse_entries(data):
    """Parse 8-byte keymap entries from response data (after 10-byte header)."""
    entries = []
    raw = data[10:]
    for i in range(0, len(raw), 8):
        e = raw[i:i+8]
        if len(e) < 8:
            break
        entries.append({
            'idx': e[0],
            'type1': e[1],
            'type2': e[2],
            'pad': e[3],
            'usage': e[4],
            'extra': e[5:8],
            'name': KEY_NAMES.get(e[4], f'0x{e[4]:02X}'),
        })
    return entries


print("=" * 60)
print("Razer Joro — Full Keymap Validation")
print("=" * 60)

# --- Metadata commands ---
print("\n--- Metadata (class 0x02) ---")
for cmd in [0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88]:
    try:
        resp = send_recv(0x02, cmd, 0)
        ds = resp[5]
        data = resp[8:8+ds]
        status = resp[0]
        if status == 0x05:
            continue  # not supported
        print(f"  0x02/0x{cmd:02X}: status=0x{status:02X} size={ds} "
              f"data=[{' '.join(f'{b:02X}' for b in data)}]")
    except Exception as e:
        print(f"  0x02/0x{cmd:02X}: {e}")

# --- Read keymap without args (default page) ---
print("\n--- Default keymap (0x02/0x8F, no args) ---")
resp = send_recv(0x02, 0x8F, 0)
ds = resp[5]
data = resp[8:8+ds]
header = data[:10]
print(f"  Header: [{' '.join(f'{b:02X}' for b in header)}]")
for e in parse_entries(data):
    print(f"  idx={e['idx']:3d}  usage=0x{e['usage']:02X}  {e['name']:12s}  "
          f"type={e['type1']:02X}/{e['type2']:02X}  "
          f"extra=[{' '.join(f'{b:02X}' for b in e['extra'])}]")

# --- Try paginated reads: arg[0] = row index ---
print("\n--- Paginated reads (arg[0] = row) ---")
all_entries = []
for row in range(20):
    try:
        resp = send_recv(0x02, 0x8F, 2, bytes([row, 0x00]))
        ds = resp[5]
        status = resp[0]
        data = resp[8:8+ds]
        if ds <= 10:
            if row < 12:  # Only print empties for reasonable range
                print(f"  Row {row:2d}: empty (status=0x{status:02X}, {ds} bytes)")
            continue
        entries = parse_entries(data)
        if not entries:
            continue
        header = data[:10]
        keys_str = ", ".join(f"{e['idx']}:{e['name']}" for e in entries)
        print(f"  Row {row:2d}: [{' '.join(f'{b:02X}' for b in header[:4])}...] "
              f"{len(entries)} keys: {keys_str}")
        all_entries.extend(entries)
    except Exception as e:
        print(f"  Row {row:2d}: {e}")

# --- Try using 0x02/0x03 to set page before reading ---
print("\n--- Set page via 0x02/0x03 then read 0x8F ---")
for page in range(12):
    try:
        send_only(0x02, 0x03, 4, bytes([page, 0x00, 0x00, 0x00]))
        time.sleep(0.02)
        resp = send_recv(0x02, 0x8F, 0)
        ds = resp[5]
        status = resp[0]
        data = resp[8:8+ds]
        if ds <= 10:
            print(f"  Page {page:2d}: empty")
            continue
        entries = parse_entries(data)
        if not entries:
            continue
        first_idx = entries[0]['idx']
        last_idx = entries[-1]['idx']
        keys_str = ", ".join(f"{e['idx']}:{e['name']}" for e in entries)
        print(f"  Page {page:2d}: {len(entries)} keys (idx {first_idx}-{last_idx}): {keys_str}")
        # Add entries we haven't seen yet
        seen_idx = {e['idx'] for e in all_entries}
        for e in entries:
            if e['idx'] not in seen_idx:
                all_entries.append(e)
                seen_idx.add(e['idx'])
    except Exception as e:
        print(f"  Page {page:2d}: {e}")

# --- Summary ---
print("\n" + "=" * 60)
print(f"Total unique entries found: {len(all_entries)}")
print("=" * 60)

# Sort by index
all_entries.sort(key=lambda e: e['idx'])

# Print full map
print("\nFull keymap (sorted by index):")
for e in all_entries:
    marker = " *** CAPSLOCK ***" if e['usage'] == 0x39 else ""
    print(f"  idx={e['idx']:3d}  usage=0x{e['usage']:02X}  {e['name']:12s}  "
          f"type={e['type1']:02X}/{e['type2']:02X}{marker}")

# Highlight CapsLock if found
caps = [e for e in all_entries if e['usage'] == 0x39]
if caps:
    print(f"\n>>> CapsLock found at index {caps[0]['idx']} <<<")
else:
    print("\n>>> CapsLock NOT found in read entries <<<")
    print("    May need to check more pages/rows or use brute-force approach")
