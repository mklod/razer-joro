#!/usr/bin/env python3
"""Test how to UNHOOK a scancode — restore MM key behavior.
Tries several candidate mechanisms in sequence, pausing so the user can test
F8 between each attempt."""
import ctypes, ctypes.wintypes as wt, sys, time

k32 = ctypes.WinDLL("kernel32", use_last_error=True)
setupapi = ctypes.WinDLL("setupapi", use_last_error=True)

CreateFileW = k32.CreateFileW
CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
CreateFileW.restype = wt.HANDLE
DeviceIoControl = k32.DeviceIoControl
DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
DeviceIoControl.restype = wt.BOOL
CloseHandle = k32.CloseHandle

GENERIC_READ = 0x80000000; GENERIC_WRITE = 0x40000000
FILE_SHARE_READ = 1; FILE_SHARE_WRITE = 2; OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = wt.HANDLE(-1).value

class GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16),
                ("Data3", ctypes.c_uint16), ("Data4", ctypes.c_ubyte * 8)]

RZCONTROL_GUID = GUID(0xe3be005d, 0xd130, 0x4910,
    (ctypes.c_ubyte * 8)(0x88, 0xff, 0x09, 0xae, 0x02, 0xf6, 0x80, 0xe9))

class SP_DID(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID),
                ("Flags", wt.DWORD), ("Reserved", ctypes.c_void_p)]

setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiGetClassDevsW.restype = wt.HANDLE
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID), wt.DWORD, ctypes.POINTER(SP_DID)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(SP_DID), ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

def find_device():
    hinfo = setupapi.SetupDiGetClassDevsW(ctypes.byref(RZCONTROL_GUID), None, None, 0x12)
    di = SP_DID(); di.cbSize = ctypes.sizeof(SP_DID)
    if not setupapi.SetupDiEnumDeviceInterfaces(hinfo, None, ctypes.byref(RZCONTROL_GUID), 0, ctypes.byref(di)):
        return None
    req = wt.DWORD(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), None, 0, ctypes.byref(req), None)
    buf = (ctypes.c_ubyte * req.value)()
    ctypes.memmove(buf, ctypes.byref(wt.DWORD(8)), 4)
    if setupapi.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), buf, req.value, None, None):
        return ctypes.wstring_at(ctypes.addressof(buf) + 4)
    return None

def open_rzc():
    p = find_device()
    if not p:
        print("No rzcontrol device"); sys.exit(1)
    h = CreateFileW(p, GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h == 0:
        print(f"CreateFile err={ctypes.get_last_error()}"); sys.exit(1)
    return h

def ioctl(h, code, in_buf, in_len):
    br = wt.DWORD(0)
    r = DeviceIoControl(h, code, in_buf, in_len, None, 0, ctypes.byref(br), None)
    return r, ctypes.get_last_error() if not r else 0

def set_input_hook(h, scancode, flag):
    buf = (ctypes.c_uint8 * 292)()
    buf[4] = flag
    buf[0x0a] = scancode & 0xff
    buf[0x0b] = (scancode >> 8) & 0xff
    return ioctl(h, 0x88883024, buf, 292)

def pause(msg):
    print(f"\n>>> {msg}")
    input("    Press Enter here after testing... ")

# Main
h = open_rzc()
print(f"Opened rzcontrol, handle=0x{h:x}\n")

print("Test 1: SetInputHook(F8 scancode 0x42, flag=0)")
r, e = set_input_hook(h, 0x42, 0x00)
print(f"  result={r} err={e}")
pause("Press F8 — does monitor brightness OSD appear? (yes = unhooked, no = still filtered)")

print("\nTest 2: EnableInputHook(0) — disable whole filter")
zero4 = (ctypes.c_uint8 * 4)(0, 0, 0, 0)
r, e = ioctl(h, 0x88883034, zero4, 4)
print(f"  result={r} err={e}")
pause("Press F8 — does monitor brightness OSD appear now?")

print("\nTest 3: Try IOCTL function 0xC0A (0x88883028) — possible 'UnsetInputHook'")
buf = (ctypes.c_uint8 * 292)()
buf[0x0a] = 0x42  # F8
r, e = ioctl(h, 0x88883028, buf, 292)
print(f"  result={r} err={e}")
pause("Press F8 — does monitor brightness OSD appear now?")

print("\nTest 4: Try IOCTL function 0xC0B (0x8888302c)")
r, e = ioctl(h, 0x8888302c, buf, 292)
print(f"  result={r} err={e}")
pause("Press F8 — does monitor brightness OSD appear now?")

print("\nTest 5: Re-enable + re-hook (reset to Fn-keys mode)")
one4 = (ctypes.c_uint8 * 4)(1, 0, 0, 0)
r, e = ioctl(h, 0x88883034, one4, 4)
print(f"  EnableInputHook(1): result={r} err={e}")
r, e = set_input_hook(h, 0x42, 0x01)
print(f"  SetInputHook(F8, 1): result={r} err={e}")
pause("Press F8 — should be back to VK_F8 (dead in notepad, 'resume' in devtools)")

CloseHandle(h)
print("\nDone.")
