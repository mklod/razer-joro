"""
Open the dongle's rzcontrol device, enable ONLY EnableInputNotify
(0x88883038, NOT EnableInputHook 0x88883034), and read events from
0x88883018 for 15 seconds. Print every event hex.

If keys still work AND we see Fn events, this is our channel.
If keys are blocked, abort early (Ctrl+C and unplug dongle).
"""
import ctypes, sys, time
from ctypes import wintypes
import threading

# IOCTLs (METHOD_BUFFERED, FILE_DEVICE_UNKNOWN=0x8888)
IOCTL_READ_EVENT          = 0x88883018
IOCTL_ENABLE_INPUT_HOOK   = 0x88883034  # we DON'T call this
IOCTL_ENABLE_INPUT_NOTIFY = 0x88883038

GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
setupapi = ctypes.WinDLL('setupapi', use_last_error=True)

CreateFileW = kernel32.CreateFileW
CreateFileW.restype  = wintypes.HANDLE
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                        wintypes.HANDLE]

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]

DeviceIoControl = kernel32.DeviceIoControl
DeviceIoControl.restype  = wintypes.BOOL
DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD,
                            ctypes.c_void_p, wintypes.DWORD,
                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

# rzcontrol device GUID
RZCONTROL_GUID = "{e3be005d-d130-4910-88ff-09ae02f680e9}"

def find_rzcontrol_path():
    """Use SetupDi to find a Joro dongle rzcontrol device path."""
    GUID = ctypes.c_byte * 16
    g = (GUID)(0x5d, 0x00, 0xbe, 0xe3, 0x30, 0xd1, 0x10, 0x49,
               0x88, 0xff, 0x09, 0xae, 0x02, 0xf6, 0x80, 0xe9)
    DIGCF_DEVICEINTERFACE = 0x10
    DIGCF_PRESENT = 0x02
    SetupDiGetClassDevsW = setupapi.SetupDiGetClassDevsW
    SetupDiGetClassDevsW.restype  = wintypes.HANDLE
    SetupDiGetClassDevsW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                     wintypes.HWND, wintypes.DWORD]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD),
                    ('InterfaceClassGuid', GUID),
                    ('Flags', wintypes.DWORD),
                    ('Reserved', ctypes.c_void_p)]

    SetupDiEnumDeviceInterfaces = setupapi.SetupDiEnumDeviceInterfaces
    SetupDiEnumDeviceInterfaces.restype  = wintypes.BOOL
    SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                                            ctypes.POINTER(GUID), wintypes.DWORD,
                                            ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]

    SetupDiGetDeviceInterfaceDetailW = setupapi.SetupDiGetDeviceInterfaceDetailW
    SetupDiGetDeviceInterfaceDetailW.restype  = wintypes.BOOL
    SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE,
                                                 ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                 ctypes.c_void_p, wintypes.DWORD,
                                                 ctypes.POINTER(wintypes.DWORD),
                                                 ctypes.c_void_p]

    SetupDiDestroyDeviceInfoList = setupapi.SetupDiDestroyDeviceInfoList
    SetupDiDestroyDeviceInfoList.restype  = wintypes.BOOL
    SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]

    hDev = SetupDiGetClassDevsW(ctypes.byref(g), None, None, DIGCF_DEVICEINTERFACE | DIGCF_PRESENT)
    if hDev == INVALID_HANDLE_VALUE or hDev == 0:
        return None

    paths = []
    idx = 0
    while True:
        did = SP_DEVICE_INTERFACE_DATA()
        did.cbSize = ctypes.sizeof(did)
        ok = SetupDiEnumDeviceInterfaces(hDev, None, ctypes.byref(g), idx, ctypes.byref(did))
        if not ok:
            break
        # First call to get size
        req = wintypes.DWORD(0)
        SetupDiGetDeviceInterfaceDetailW(hDev, ctypes.byref(did), None, 0, ctypes.byref(req), None)
        if req.value == 0:
            idx += 1; continue
        # Allocate detail buffer: layout = DWORD cbSize + WCHAR DevicePath[1]
        detail_buf = (ctypes.c_byte * req.value)()
        # cbSize must be 6 on 64-bit (sizeof DWORD) + 2 (anonymous? padding?) - actually MSDN says cbSize=6 on 32-bit and 8 on 64-bit
        ctypes.cast(detail_buf, ctypes.POINTER(wintypes.DWORD))[0] = 8
        ok = SetupDiGetDeviceInterfaceDetailW(hDev, ctypes.byref(did), detail_buf, req.value, None, None)
        if ok:
            # path begins at offset 4 (DWORD cbSize), as WCHAR
            path = ctypes.wstring_at(ctypes.addressof(detail_buf) + 4)
            paths.append(path)
        idx += 1

    SetupDiDestroyDeviceInfoList(hDev)
    # Filter to dongle (vid_1532 pid_009c)
    dongle = [p for p in paths if 'vid_1532' in p.lower() and 'pid_009c' in p.lower()]
    return dongle[0] if dongle else (paths[0] if paths else None)


def main(seconds=15):
    path = find_rzcontrol_path()
    if not path:
        print("No rzcontrol device found")
        return
    print(f"Opening rzcontrol: {path}")

    h = CreateFileW(path,
                    GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h == 0:
        err = ctypes.get_last_error()
        print(f"CreateFileW failed: WinError={err}")
        return
    print(f"Opened, handle={h:#x}")

    # Enable input notify (NOT input hook)
    val = (ctypes.c_uint32 * 1)(1)
    bytes_returned = wintypes.DWORD(0)
    ok = DeviceIoControl(h, IOCTL_ENABLE_INPUT_NOTIFY,
                         val, 4,
                         None, 0,
                         ctypes.byref(bytes_returned), None)
    if not ok:
        err = ctypes.get_last_error()
        print(f"EnableInputNotify failed: WinError={err}")
        CloseHandle(h)
        return
    print(f"EnableInputNotify(true) OK")

    # Read events in a loop
    print(f"\nReading events for {seconds}s. Hold Fn for ~3s, release ~3s. PRESS CTRL+C IF KEYBOARD STOPS WORKING.\n", flush=True)
    out = (ctypes.c_byte * 304)()
    t_end = time.time() + seconds
    n = 0
    while time.time() < t_end:
        ctypes.memset(out, 0, 304)
        bytes_returned.value = 0
        ok = DeviceIoControl(h, IOCTL_READ_EVENT,
                             None, 0,
                             out, 304,
                             ctypes.byref(bytes_returned), None)
        if ok:
            n += 1
            ts = time.strftime("%H:%M:%S")
            data = bytes(out[:bytes_returned.value])
            hexed = ' '.join(f'{b:02x}' for b in data[:64])
            print(f"{ts}  EVT#{n}  ({bytes_returned.value}B) {hexed}", flush=True)
        else:
            err = ctypes.get_last_error()
            if err != 0:
                # On STATUS_PENDING (async), DeviceIoControl returns 0; for a
                # synchronous handle the read should complete. Print non-zero
                # errors.
                if err not in (997,):  # ERROR_IO_PENDING
                    pass  # silent, would spam
            time.sleep(0.005)

    print(f"\n=== {n} events captured. Disabling notify... ===")
    val[0] = 0
    DeviceIoControl(h, IOCTL_ENABLE_INPUT_NOTIFY, val, 4, None, 0,
                    ctypes.byref(bytes_returned), None)
    CloseHandle(h)
    print("Done.")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 15)
