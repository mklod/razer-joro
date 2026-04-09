# proto/scan_keymap.py
# Last modified: 2026-04-09--1600
"""Decode keymap table from Joro firmware."""

import time
import usb.core
import usb.backend.libusb1

dll_path = r"C:\Users\mklod\AppData\Local\razer-joro-venv\Lib\site-packages\libusb\_platform\windows\x86_64\libusb-1.0.dll"
backend = usb.backend.libusb1.get_backend(find_library=lambda x: dll_path)
dev = usb.core.find(idVendor=0x1532, idProduct=0x02CD, backend=backend)

KEY_NAMES = {
    0x04:'A',0x05:'B',0x06:'C',0x07:'D',0x08:'E',0x09:'F',0x0A:'G',0x0B:'H',
    0x0C:'I',0x0D:'J',0x0E:'K',0x0F:'L',0x10:'M',0x11:'N',0x12:'O',0x13:'P',
    0x14:'Q',0x15:'R',0x16:'S',0x17:'T',0x18:'U',0x19:'V',0x1A:'W',0x1B:'X',
    0x1C:'Y',0x1D:'Z',0x1E:'1',0x1F:'2',0x20:'3',0x21:'4',0x22:'5',0x23:'6',
    0x24:'7',0x25:'8',0x26:'9',0x27:'0',0x28:'Enter',0x29:'Esc',0x2A:'Bksp',
    0x2B:'Tab',0x2C:'Space',0x2D:'-',0x2E:'=',0x2F:'[',0x30:']',0x31:'\\',
    0x33:';',0x34:"'",0x35:'`',0x36:',',0x37:'.',0x38:'/',0x39:'CapsLock',
    0x3A:'F1',0x3B:'F2',0x3C:'F3',0x3D:'F4',0x3E:'F5',0x3F:'F6',
    0x40:'F7',0x41:'F8',0x42:'F9',0x43:'F10',0x44:'F11',0x45:'F12',
    0x46:'PrtScr',0x47:'ScrLk',0x48:'Pause',0x49:'Ins',0x4A:'Home',
    0x4B:'PgUp',0x4C:'Del',0x4D:'End',0x4E:'PgDn',0x4F:'Right',
    0x50:'Left',0x51:'Down',0x52:'Up',0x53:'NumLk',
    0xE0:'LCtrl',0xE1:'LShift',0xE2:'LAlt',0xE3:'LGui',
    0xE4:'RCtrl',0xE5:'RShift',0xE6:'RAlt',0xE7:'RGui',
}


def send_cmd(cmd_class, cmd_id, data_size=0, args=b''):
    buf = bytearray(90)
    buf[0] = 0x00; buf[1] = 0x1F; buf[5] = data_size; buf[6] = cmd_class; buf[7] = cmd_id
    for i, b in enumerate(args[:80]):
        buf[8 + i] = b
    crc = 0
    for b in buf[2:88]:
        crc ^= b
    buf[88] = crc
    dev.ctrl_transfer(0x21, 0x09, 0x0300, 0x03, bytes(buf))
    time.sleep(0.01)
    return dev.ctrl_transfer(0xA1, 0x01, 0x0300, 0x03, 90)


# Read default keymap
print("=== Keymap 0x02/0x8F (no args) ===")
resp = send_cmd(0x02, 0x8F, 0)
ds = resp[5]
data = bytes(resp[8:8+ds])
print(f"Size: {ds} bytes")
print(f"Raw: {' '.join(f'{b:02X}' for b in data)}")
print()

# Parse header
header = data[:10]
print(f"Header: {' '.join(f'{b:02X}' for b in header)}")

# Parse 8-byte entries
entries = data[10:]
print(f"\nEntries ({len(entries)//8}):")
for i in range(0, len(entries), 8):
    entry = entries[i:i+8]
    if len(entry) < 8:
        break
    idx = entry[0]
    t1, t2, pad = entry[1], entry[2], entry[3]
    usage = entry[4]
    name = KEY_NAMES.get(usage, f"0x{usage:02X}")
    extra = entry[5:8]
    print(f"  [{idx:3d}] type={t1:02X}/{t2:02X} usage=0x{usage:02X} ({name:8s}) extra=[{' '.join(f'{b:02X}' for b in extra)}]")

# Try paginated reads
print("\n=== Paginated reads (page in arg[0]) ===")
for page in range(20):
    resp = send_cmd(0x02, 0x8F, 1, bytes([page]))
    ds = resp[5]
    data = bytes(resp[8:8+ds])
    if ds > 10:
        n_entries = (ds - 10) // 8
        first_usage = data[14] if ds > 14 else 0
        last_usage = data[10 + (n_entries-1)*8 + 4] if n_entries > 0 else 0
        first_name = KEY_NAMES.get(first_usage, f"0x{first_usage:02X}")
        last_name = KEY_NAMES.get(last_usage, f"0x{last_usage:02X}")
        print(f"  Page {page:2d}: {n_entries} entries, first={first_name}, last={last_name}")
    elif ds > 0:
        print(f"  Page {page:2d}: {ds} bytes [{' '.join(f'{b:02X}' for b in data)}]")
    else:
        print(f"  Page {page:2d}: empty")

# Also check 0x02/0x82 which returned [0A 00 0A 00] - might be keymap metadata
print("\n=== 0x02/0x82 (keymap metadata?) ===")
resp = send_cmd(0x02, 0x82, 0)
ds = resp[5]
data = bytes(resp[8:8+ds])
print(f"Data: [{' '.join(f'{b:02X}' for b in data)}]")
print(f"Parsed: rows={data[0]}, ?, total_rows={data[2]}, ?")
