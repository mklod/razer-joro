#!/usr/bin/env python3
"""Razer RzDev_02ce filter driver IOCTL client — PoC for Joro BLE fn-primary.

Proves user-mode can drive the Razer lower-filter driver via DeviceIoControl on
the rzcontrol# device interface. Reproduces Synapse's fn-primary mechanism
without needing Synapse running, without BLE writes, without any firmware work.

Verified 2026-04-14:
  - flag=1 installs a filter rule: scancode X is translated to its natural
    function-key VK (e.g. 0x42 -> VK_F8). Windows' consumer handler stops
    seeing the brightness usage, so the OSD doesn't fire. DevTools F8 = resume.
  - flag=0 on the same scancode RESTORES MM behavior (brightness works again).
  - F1/F2/F3 (BLE slot keys) are firmware-locked — filter doesn't affect them.

Usage:
  python rzcontrol_poc.py hook F8 F9 F10 F11 F12
  python rzcontrol_poc.py unhook F8 F9 F10 F11 F12
  python rzcontrol_poc.py enable        # EnableInputHook(1) + EnableInputNotify(1)
  python rzcontrol_poc.py disable       # EnableInputHook(0)
"""
import ctypes, ctypes.wintypes as wt, sys

# ── Scancode table (PS/2 Set 1) ──
SCANCODES = {
    'ESC': 0x01, 'TAB': 0x0f, 'LALT': 0x38,
    'F1': 0x3b, 'F2': 0x3c, 'F3': 0x3d, 'F4': 0x3e, 'F5': 0x3f, 'F6': 0x40,
    'F7': 0x41, 'F8': 0x42, 'F9': 0x43, 'F10': 0x44, 'F11': 0x57, 'F12': 0x58,
    'KPHOME': 0x47, 'KPPGUP': 0x49, 'KPEND': 0x4f, 'KPPGDN': 0x51,
}

# ── IOCTLs (device type 0x8888, METHOD_BUFFERED, FILE_ANY_ACCESS) ──
IOCTL_ENABLE_INPUT_HOOK   = 0x88883034  # in: 4 bytes bool
IOCTL_ENABLE_INPUT_NOTIFY = 0x88883038  # in: 4 bytes bool
IOCTL_SET_INPUT_HOOK      = 0x88883024  # in: 292 byte struct

# ── Win32 FFI ──
k32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

class GUID(ctypes.Structure):
    _fields_ = [("D1", ctypes.c_uint32), ("D2", ctypes.c_uint16),
                ("D3", ctypes.c_uint16), ("D4", ctypes.c_ubyte * 8)]

# {e3be005d-d130-4910-88ff-09ae02f680e9}
RZCONTROL_GUID = GUID(0xe3be005d, 0xd130, 0x4910,
    (ctypes.c_ubyte * 8)(0x88, 0xff, 0x09, 0xae, 0x02, 0xf6, 0x80, 0xe9))

class SP_DID(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p,
    ctypes.POINTER(GUID), wt.DWORD, ctypes.POINTER(SP_DID)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DID),
    ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
    ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k32.DeviceIoControl.restype = wt.BOOL

GENERIC_RW = 0xC0000000
FILE_SHARE_RW = 3
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value
DIGCF = 0x12  # DIGCF_PRESENT | DIGCF_DEVICEINTERFACE

# ── Enumerate rzcontrol device paths (find the BLE Joro one) ──
def find_rzcontrol_ble():
    hinfo = setupapi.SetupDiGetClassDevsW(ctypes.byref(RZCONTROL_GUID), None, None, DIGCF)
    if hinfo == INVALID_HANDLE_VALUE:
        raise OSError(f"SetupDiGetClassDevs failed: {ctypes.get_last_error()}")
    i = 0
    while True:
        di = SP_DID(); di.cbSize = ctypes.sizeof(SP_DID)
        if not setupapi.SetupDiEnumDeviceInterfaces(hinfo, None, ctypes.byref(RZCONTROL_GUID), i, ctypes.byref(di)):
            return None
        req = wt.DWORD(0)
        setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), None, 0, ctypes.byref(req), None)
        buf = (ctypes.c_ubyte * req.value)()
        ctypes.memmove(buf, ctypes.byref(wt.DWORD(8)), 4)
        if setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), buf, req.value, None, None):
            path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
            if "vid_068e" in path.lower() and "pid_02ce" in path.lower():
                return path
        i += 1

def open_rzc():
    path = find_rzcontrol_ble()
    if not path:
        raise RuntimeError("No Joro BLE rzcontrol device present — is Joro connected over BLE?")
    h = k32.CreateFileW(path, GENERIC_RW, FILE_SHARE_RW, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h == 0:
        raise OSError(f"CreateFile failed: {ctypes.get_last_error()}")
    return h, path

def ioctl(h, code, buf, length):
    br = wt.DWORD(0)
    ok = k32.DeviceIoControl(h, code, buf, length, None, 0, ctypes.byref(br), None)
    return ok, (ctypes.get_last_error() if not ok else 0), br.value

def enable_filter(h, enable):
    buf = (ctypes.c_uint8 * 4)(1 if enable else 0, 0, 0, 0)
    r, e, _ = ioctl(h, IOCTL_ENABLE_INPUT_HOOK, buf, 4)
    if not r: raise OSError(f"EnableInputHook({int(enable)}) failed: {e}")
    r, e, _ = ioctl(h, IOCTL_ENABLE_INPUT_NOTIFY, buf, 4)
    if not r: raise OSError(f"EnableInputNotify({int(enable)}) failed: {e}")

def hook_key(h, scancode, active):
    """active=True installs a filter rule that translates the scancode to its
    natural function-key VK and swallows the consumer usage. active=False
    unregisters the hook, restoring default (MM) behavior."""
    buf = (ctypes.c_uint8 * 292)()
    buf[4] = 1 if active else 0
    buf[0x0a] = scancode & 0xff
    buf[0x0b] = (scancode >> 8) & 0xff
    r, e, _ = ioctl(h, IOCTL_SET_INPUT_HOOK, buf, 292)
    if not r: raise OSError(f"SetInputHook(sc=0x{scancode:02x}, flag={int(active)}) failed: {e}")

# ── CLI ──
def parse_scancodes(args):
    out = []
    for a in args:
        n = a.upper()
        if n in SCANCODES:
            out.append((n, SCANCODES[n]))
        else:
            try:
                out.append((f"0x{int(a, 0):02x}", int(a, 0)))
            except ValueError:
                print(f"Unknown key: {a}")
                sys.exit(2)
    return out

def usage():
    print(__doc__)
    sys.exit(1)

if len(sys.argv) < 2:
    usage()

cmd = sys.argv[1].lower()
h, path = open_rzc()
print(f"Opened {path}")
print(f"Handle: 0x{h:x}")

try:
    if cmd in ("enable", "disable"):
        enable = (cmd == "enable")
        enable_filter(h, enable)
        print(f"[OK] {'Enable' if enable else 'Disable'}InputHook + {'Enable' if enable else 'Disable'}InputNotify")
    elif cmd == "hook":
        if len(sys.argv) < 3:
            print("usage: hook <key1> [key2 ...]")
            sys.exit(1)
        enable_filter(h, True)
        for name, sc in parse_scancodes(sys.argv[2:]):
            hook_key(h, sc, True)
            print(f"[OK] hook {name} (scancode 0x{sc:02x}) -> function-key VK")
    elif cmd == "unhook":
        if len(sys.argv) < 3:
            print("usage: unhook <key1> [key2 ...]")
            sys.exit(1)
        for name, sc in parse_scancodes(sys.argv[2:]):
            hook_key(h, sc, False)
            print(f"[OK] unhook {name} (scancode 0x{sc:02x}) -> restored to MM behavior")
    else:
        usage()
finally:
    k32.CloseHandle(h)
