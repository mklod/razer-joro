#!/usr/bin/env python3
"""Surgical probe of rzcontrol IOCTL state. Uses overlapped I/O for
0x88883018 reads so we can distinguish PENDING (queued, good) from
BAD_COMMAND (rejected, bad) without blocking.

Each trial opens a fresh handle, runs a sequence of writes, then posts
an overlapped read and waits 300ms. Outcome is one of:
  - COMPLETED: read completed with an event in that window
  - PENDING:   read queued OK, just no event yet (GOOD - filter accepting)
  - REJECTED:  read returned an error immediately (BAD)
"""
import ctypes, ctypes.wintypes as wt, time

IOCTL_READ_EVENT         = 0x88883018
IOCTL_CMD                = 0x88883020
IOCTL_SET_INPUT_HOOK     = 0x88883024
IOCTL_REDIRECT           = 0x8888301C
IOCTL_ENUM_HOOK          = 0x88883030
IOCTL_ENABLE_INPUT_HOOK  = 0x88883034
IOCTL_ENABLE_INPUT_NOTIFY= 0x88883038

FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
ERROR_BAD_COMMAND = 22
WAIT_TIMEOUT = 258

k32 = ctypes.WinDLL('kernel32', use_last_error=True)
setupapi = ctypes.WinDLL('setupapi', use_last_error=True)

class G(ctypes.Structure):
    _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16), ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]
class D(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('g', G), ('f', wt.DWORD), ('r', ctypes.c_void_p)]

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ('Internal', ctypes.c_void_p),
        ('InternalHigh', ctypes.c_void_p),
        ('Offset', wt.DWORD),
        ('OffsetHigh', wt.DWORD),
        ('hEvent', wt.HANDLE),
    ]

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(G), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(G), wt.DWORD, ctypes.POINTER(D)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(D), ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.POINTER(OVERLAPPED)]
k32.DeviceIoControl.restype = wt.BOOL
k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
k32.CreateEventW.restype = wt.HANDLE
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.WaitForSingleObject.restype = wt.DWORD
k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED)]
k32.CancelIoEx.restype = wt.BOOL

GUID = G(0xe3be005d, 0xd130, 0x4910, (ctypes.c_ubyte*8)(0x88,0xff,0x09,0xae,0x02,0xf6,0x80,0xe9))

def find_path():
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(GUID), None, None, 0x12)
    di = D(); di.cbSize = ctypes.sizeof(D)
    setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(GUID), 0, ctypes.byref(di))
    req = wt.DWORD(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(di), None, 0, ctypes.byref(req), None)
    buf = (ctypes.c_ubyte * req.value)()
    ctypes.memmove(buf, ctypes.byref(wt.DWORD(8)), 4)
    setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(di), buf, req.value, None, None)
    return ctypes.wstring_at(ctypes.addressof(buf) + 4)

def open_rzc():
    """Open with FILE_FLAG_OVERLAPPED so we can issue async reads."""
    p = find_path()
    h = k32.CreateFileW(p, 0xC0000000, 3, None, 3, FILE_FLAG_OVERLAPPED, None)
    if h == 0 or h == -1:
        raise OSError(f"CreateFile failed: {ctypes.get_last_error()}")
    return h

def ioctl_w(h, code, input_bytes):
    buf = (ctypes.c_uint8 * len(input_bytes))(*input_bytes)
    br = wt.DWORD(0)
    ov = OVERLAPPED()
    ov.hEvent = k32.CreateEventW(None, True, False, None)
    ok = k32.DeviceIoControl(h, code, buf, len(input_bytes), None, 0, ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.get_last_error()
    if not ok and err == ERROR_IO_PENDING:
        # Wait briefly — writes should complete almost immediately
        k32.WaitForSingleObject(ov.hEvent, 200)
    k32.CloseHandle(ov.hEvent)
    return ok or err == ERROR_IO_PENDING, err

def read_with_timeout(h, timeout_ms=300):
    """Issue an overlapped 0x88883018 read and wait timeout_ms.
    Returns one of: 'COMPLETED', 'PENDING', 'REJECTED:<err>'.
    """
    out = (ctypes.c_uint8 * 304)()
    br = wt.DWORD(0)
    ov = OVERLAPPED()
    ov.hEvent = k32.CreateEventW(None, True, False, None)
    ok = k32.DeviceIoControl(h, IOCTL_READ_EVENT, None, 0, out, 304, ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.get_last_error()
    if ok:
        k32.CloseHandle(ov.hEvent)
        return f"COMPLETED br={br.value}"
    if err == ERROR_IO_PENDING:
        wait = k32.WaitForSingleObject(ov.hEvent, timeout_ms)
        if wait == 0:
            k32.CloseHandle(ov.hEvent)
            return f"COMPLETED br={br.value}"
        elif wait == WAIT_TIMEOUT:
            # Still pending — GOOD, the filter accepted the read
            k32.CancelIoEx(h, ctypes.byref(ov))
            k32.WaitForSingleObject(ov.hEvent, 100)
            k32.CloseHandle(ov.hEvent)
            return "PENDING"
        else:
            k32.CloseHandle(ov.hEvent)
            return f"WAIT_ERR={wait}"
    k32.CloseHandle(ov.hEvent)
    return f"REJECTED err={err}"

def enable(h, on=True):
    return ioctl_w(h, IOCTL_ENABLE_INPUT_HOOK, [1 if on else 0, 0, 0, 0])

def notify(h, on=True):
    return ioctl_w(h, IOCTL_ENABLE_INPUT_NOTIFY, [1 if on else 0, 0, 0, 0])

def set_hook(h, sc, flag=1):
    buf = [0] * 292
    buf[4] = flag
    buf[0x0a] = sc & 0xff
    buf[0x0b] = (sc >> 8) & 0xff
    return ioctl_w(h, IOCTL_SET_INPUT_HOOK, buf)

def cmd_0a(h, usage):
    buf = [0] * 32
    buf[4] = 0x0a
    buf[8] = usage & 0xff
    buf[9] = (usage >> 8) & 0xff
    return ioctl_w(h, IOCTL_CMD, buf)

def redirect_input(h, payload_bytes):
    return ioctl_w(h, IOCTL_REDIRECT, payload_bytes)

def trial(name, fn):
    print(f"\n=== TRIAL {name} ===")
    h = open_rzc()
    try:
        result = fn(h)
        print(f"  RESULT: {result}")
    finally:
        k32.CloseHandle(h)
    time.sleep(0.1)

def t1(h):
    return read_with_timeout(h)

def t2(h):
    enable(h); notify(h)
    return read_with_timeout(h)

def t3(h):
    r0 = read_with_timeout(h, 50)
    enable(h); notify(h)
    return f"cold={r0}; post-enable={read_with_timeout(h)}"

def t4(h):
    enable(h); notify(h); set_hook(h, 0x3f)
    return read_with_timeout(h)

def t5(h):
    enable(h); notify(h)
    for sc in (0x3f, 0x40, 0x41, 0x42, 0x43, 0x44, 0x57, 0x58):
        set_hook(h, sc)
    return read_with_timeout(h)

def t6(h):
    # Probe the unknown IOCTLs with various payloads
    r = []
    for size in (4, 8, 16, 32):
        ok, err = redirect_input(h, [0] * size)
        r.append(f"redirect[{size}]={'OK' if ok else f'err={err}'}")
    return '; '.join(r)

def t7(h):
    enable(h); notify(h); cmd_0a(h, 0x0070); cmd_0a(h, 0x006f)
    return read_with_timeout(h)

def t8(h):
    # Enable without Notify
    enable(h)
    return read_with_timeout(h)

def t9(h):
    # Notify without Enable
    notify(h)
    return read_with_timeout(h)

def t10(h):
    # Notify before Enable
    notify(h); enable(h)
    return read_with_timeout(h)

trials = [
    ("1 cold read",                            t1),
    ("2 enable+notify, read",                  t2),
    ("3 cold read THEN enable+notify, read",   t3),
    ("4 enable+notify+hook(F5), read",         t4),
    ("5 enable+notify+hook(F5..F12), read",    t5),
    ("6 probe redirect IOCTL",                 t6),
    ("7 enable+notify+cmd=0x0a, read",         t7),
    ("8 enable ONLY, read",                    t8),
    ("9 notify ONLY, read",                    t9),
    ("10 notify BEFORE enable, read",          t10),
]
for name, fn in trials:
    try:
        trial(name, fn)
    except Exception as e:
        print(f"  EXCEPTION: {e}")
print("\nAll trials done.")
