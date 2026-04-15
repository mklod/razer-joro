#!/usr/bin/env python3
"""Test: hold the rzcontrol handle OPEN and keep rules installed.
Opens device, installs hook on F8, waits for Ctrl+C, then unhooks and closes."""
import ctypes, ctypes.wintypes as wt, time, sys

k = ctypes.WinDLL('kernel32', use_last_error=True)
s = ctypes.WinDLL('setupapi', use_last_error=True)

class G(ctypes.Structure): _fields_=[('a',ctypes.c_uint32),('b',ctypes.c_uint16),('c',ctypes.c_uint16),('d',ctypes.c_ubyte*8)]
GID = G(0xe3be005d,0xd130,0x4910,(ctypes.c_ubyte*8)(0x88,0xff,0x09,0xae,0x02,0xf6,0x80,0xe9))
class D(ctypes.Structure): _fields_=[('cbSize',wt.DWORD),('g',G),('f',wt.DWORD),('r',ctypes.c_void_p)]

s.SetupDiGetClassDevsW.restype = wt.HANDLE
s.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(G), wt.LPCWSTR, wt.HWND, wt.DWORD]
s.SetupDiEnumDeviceInterfaces.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(G), wt.DWORD, ctypes.POINTER(D)]
s.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
s.SetupDiGetDeviceInterfaceDetailW.argtypes = [wt.HANDLE, ctypes.POINTER(D), ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
s.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

hinfo = s.SetupDiGetClassDevsW(ctypes.byref(GID), None, None, 0x12)
di = D(); di.cbSize = ctypes.sizeof(D)
s.SetupDiEnumDeviceInterfaces(hinfo, None, ctypes.byref(GID), 0, ctypes.byref(di))
req = wt.DWORD(0)
s.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), None, 0, ctypes.byref(req), None)
buf = (ctypes.c_ubyte * req.value)()
ctypes.memmove(buf, ctypes.byref(wt.DWORD(8)), 4)
s.SetupDiGetDeviceInterfaceDetailW(hinfo, ctypes.byref(di), buf, req.value, None, None)
path = ctypes.wstring_at(ctypes.addressof(buf) + 4)

k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
k.CreateFileW.restype = wt.HANDLE
k.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
k.DeviceIoControl.restype = wt.BOOL

h = k.CreateFileW(path, 0xC0000000, 3, None, 3, 0, None)
print(f"Handle opened: 0x{h:x}")

one = (ctypes.c_uint8 * 4)(1, 0, 0, 0)
br = wt.DWORD(0)

r = k.DeviceIoControl(h, 0x88883034, one, 4, None, 0, ctypes.byref(br), None)
print(f"EnableInputHook(1): r={r}")
r = k.DeviceIoControl(h, 0x88883038, one, 4, None, 0, ctypes.byref(br), None)
print(f"EnableInputNotify(1): r={r}")

# Hook F8
buf = (ctypes.c_uint8 * 292)()
buf[4] = 1
buf[0x0a] = 0x42
r = k.DeviceIoControl(h, 0x88883024, buf, 292, None, 0, ctypes.byref(br), None)
print(f"SetInputHook(F8): r={r}")

print("\n*** Handle is OPEN. Press F8 in a text editor or Chrome DevTools.")
print("*** Is F8 hooked? Ctrl+C when done testing.")
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\nUnhooking...")
    buf[4] = 0
    k.DeviceIoControl(h, 0x88883024, buf, 292, None, 0, ctypes.byref(br), None)
    k.CloseHandle(h)
    print("Done.")
