# proto/scan_keymap_full.py
# Last modified: 2026-04-09--1600
"""Read full keymap from Joro — all rows."""

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
    0x54:'Num/',0x55:'Num*',0x56:'Num-',0x57:'Num+',0x58:'NumEnter',
    0x59:'Num1',0x5A:'Num2',0x5B:'Num3',0x5C:'Num4',0x5D:'Num5',
    0x5E:'Num6',0x5F:'Num7',0x60:'Num8',0x61:'Num9',0x62:'Num0',
    0x63:'Num.',0x65:'App',0x87:'Intl1',0x88:'Intl2',0x89:'Intl3',
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


# Get metadata
resp = send_cmd(0x02, 0x82, 0)
meta = bytes(resp[8:8+resp[5]])
num_rows = meta[0]
print(f"Keymap metadata: {' '.join(f'{b:02X}' for b in meta)} ({num_rows} rows)")
print()

# Read each row — try arg[0] = row_index, arg[1] = 0x00
for row in range(num_rows):
    # Try different arg formats to get different rows
    # Format 1: row index as 2-byte arg
    resp = send_cmd(0x02, 0x8F, 2, bytes([row, 0x00]))
    ds = resp[5]
    data = bytes(resp[8:8+ds])

    if ds > 10:
        header = data[:10]
        entries = data[10:]
        n = len(entries) // 8
        print(f"Row {row}: header=[{' '.join(f'{b:02X}' for b in header)}] {n} keys:")
        for i in range(0, len(entries), 8):
            e = entries[i:i+8]
            if len(e) < 8:
                break
            idx, t1, t2, pad, usage = e[0], e[1], e[2], e[3], e[4]
            name = KEY_NAMES.get(usage, f"0x{usage:02X}")
            extra = e[5:8]
            print(f"    [{idx:3d}] t={t1:02X}/{t2:02X} usage=0x{usage:02X} ({name:10s}) [{' '.join(f'{b:02X}' for b in extra)}]")
    else:
        print(f"Row {row}: {ds} bytes [{' '.join(f'{b:02X}' for b in data)}]")
    print()
