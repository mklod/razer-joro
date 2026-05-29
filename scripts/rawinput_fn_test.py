"""
Use Win32 RawInput to subscribe to all HID input reports from Joro dongle
(VID 0x1532 PID 0x009C). Look for report ID 0x08 (Fn-state).

Hold Fn for 3s, release. If we see `08 01` and `08 00` reports, we have
host-side Fn detection through dongle.
"""
import ctypes, sys, time
from ctypes import wintypes

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

# RawInput constants
RIDEV_INPUTSINK = 0x00000100  # receive input even when not foreground
RIDEV_DEVNOTIFY = 0x00002000
RID_INPUT       = 0x10000003
RIM_TYPEHID     = 2
RIM_TYPEKEYBOARD = 1
RIM_TYPEMOUSE   = 0
RIDI_DEVICENAME = 0x20000007
RIDI_DEVICEINFO = 0x2000000b

WM_INPUT  = 0x00FF
WM_QUIT   = 0x0012
HWND_MESSAGE = ctypes.c_void_p(-3)
WS_OVERLAPPED = 0
WS_OVERLAPPEDWINDOW = 0xCF0000

# Structures
class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ('usUsagePage', ctypes.c_ushort),
        ('usUsage',     ctypes.c_ushort),
        ('dwFlags',     wintypes.DWORD),
        ('hwndTarget',  wintypes.HWND),
    ]

class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ('dwType',  wintypes.DWORD),
        ('dwSize',  wintypes.DWORD),
        ('hDevice', wintypes.HANDLE),
        ('wParam',  wintypes.WPARAM),
    ]

class RAWHID(ctypes.Structure):
    _fields_ = [
        ('dwSizeHid', wintypes.DWORD),
        ('dwCount',   wintypes.DWORD),
        ('bRawData',  ctypes.c_ubyte * 1),  # variable-length
    ]

class RAWINPUT_HID(ctypes.Structure):
    _fields_ = [
        ('header', RAWINPUTHEADER),
        ('hid',    RAWHID),
    ]

class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style',         wintypes.UINT),
        ('lpfnWndProc',   ctypes.c_void_p),
        ('cbClsExtra',    ctypes.c_int),
        ('cbWndExtra',    ctypes.c_int),
        ('hInstance',     wintypes.HINSTANCE),
        ('hIcon',         wintypes.HICON),
        ('hCursor',       wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName',  wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
    ]

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_long, wintypes.HWND, wintypes.UINT,
                             wintypes.WPARAM, wintypes.LPARAM)

DefWindowProc = user32.DefWindowProcW
DefWindowProc.restype  = ctypes.c_long
DefWindowProc.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

GetRawInputData = user32.GetRawInputData
GetRawInputData.restype  = wintypes.UINT
GetRawInputData.argtypes = [wintypes.HANDLE, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(wintypes.UINT),
                            wintypes.UINT]

GetRawInputDeviceInfoW = user32.GetRawInputDeviceInfoW
GetRawInputDeviceInfoW.restype  = wintypes.UINT
GetRawInputDeviceInfoW.argtypes = [wintypes.HANDLE, wintypes.UINT,
                                   ctypes.c_void_p, ctypes.POINTER(wintypes.UINT)]

device_names = {}  # hDevice -> name string

def get_device_name(hDevice):
    if hDevice in device_names:
        return device_names[hDevice]
    sz = wintypes.UINT(0)
    GetRawInputDeviceInfoW(hDevice, RIDI_DEVICENAME, None, ctypes.byref(sz))
    if sz.value == 0:
        return None
    buf = (ctypes.c_wchar * sz.value)()
    GetRawInputDeviceInfoW(hDevice, RIDI_DEVICENAME, buf, ctypes.byref(sz))
    name = ctypes.wstring_at(buf)
    device_names[hDevice] = name
    return name

_msg_count = [0]
def wnd_proc(hwnd, msg, wparam, lparam):
    if msg == WM_INPUT:
        _msg_count[0] += 1
        sz = wintypes.UINT(0)
        # First call: get required size
        GetRawInputData(lparam, RID_INPUT, None, ctypes.byref(sz),
                        ctypes.sizeof(RAWINPUTHEADER))
        if sz.value == 0:
            return DefWindowProc(hwnd, msg, wparam, lparam)
        buf = (ctypes.c_ubyte * sz.value)()
        GetRawInputData(lparam, RID_INPUT, buf, ctypes.byref(sz),
                        ctypes.sizeof(RAWINPUTHEADER))
        # Parse header
        hdr = ctypes.cast(buf, ctypes.POINTER(RAWINPUTHEADER))[0]
        type_name = {0:'MOUSE', 1:'KBD', 2:'HID'}.get(hdr.dwType, f'?{hdr.dwType}')
        name = get_device_name(hdr.hDevice) or '<unknown>'
        # Log every WM_INPUT regardless of type
        ts = time.strftime("%H:%M:%S")
        # Print first 32 bytes of the data (after header)
        off = ctypes.sizeof(RAWINPUTHEADER)
        rawdata = ctypes.string_at(ctypes.addressof(buf) + off, min(32, sz.value - off))
        hexed = ' '.join(f'{b:02x}' for b in rawdata)
        path_short = name[-30:] if name else ''
        print(f"{ts}  type={type_name}  ({sz.value}B) {hexed}  [{path_short}]", flush=True)
        if hdr.dwType == RIM_TYPEHID:
            if True:
                # RAWHID starts at offset sizeof(RAWINPUTHEADER)
                off = ctypes.sizeof(RAWINPUTHEADER)
                hid_size = ctypes.cast(ctypes.addressof(buf) + off,
                                       ctypes.POINTER(wintypes.DWORD))[0]
                hid_count = ctypes.cast(ctypes.addressof(buf) + off + 4,
                                        ctypes.POINTER(wintypes.DWORD))[0]
                data_off = off + 8
                # Print up to first hid_size bytes (one report)
                data = bytes(buf[data_off:data_off + hid_size])
                if data:
                    ts = time.strftime("%H:%M:%S")
                    rid = data[0] if data else 0
                    hexed = ' '.join(f'{b:02x}' for b in data[:32])
                    short_name = name.split('#')[1][:30] if '#' in name else name[-30:]
                    print(f"{ts}  rid=0x{rid:02x}  ({hid_size}B) {hexed}  [{short_name}]", flush=True)
    return DefWindowProc(hwnd, msg, wparam, lparam)

def main(seconds=20):
    proc = WNDPROC(wnd_proc)
    cls = WNDCLASSW()
    cls.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
    cls.hInstance = kernel32.GetModuleHandleW(None)
    cls.lpszClassName = "RawInputJoroTest"
    atom = user32.RegisterClassW(ctypes.byref(cls))
    if not atom:
        print(f"RegisterClassW failed: {ctypes.get_last_error()}")
        return
    user32.CreateWindowExW.restype  = wintypes.HWND
    user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                       wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_int, ctypes.c_int,
                                       wintypes.HWND, wintypes.HMENU,
                                       wintypes.HINSTANCE, ctypes.c_void_p]
    # Use a regular top-level invisible window. RIDEV_INPUTSINK can be
    # finicky with HWND_MESSAGE-only windows.
    hwnd = user32.CreateWindowExW(0, "RawInputJoroTest", "RawInputJoroTest",
                                  WS_OVERLAPPED, 0, 0, 0, 0,
                                  None, None, cls.hInstance, None)
    if not hwnd:
        print(f"CreateWindowExW failed: {ctypes.get_last_error()}")
        return

    # Register for HID input on relevant pages. RIDEV_PAGEONLY (0x20)
    # subscribes to ALL usages on the given page (use with usage=0).
    RIDEV_PAGEONLY = 0x00000020
    rids = (RAWINPUTDEVICE * 4)(
        RAWINPUTDEVICE(0x0001, 0x0006, RIDEV_INPUTSINK, hwnd),  # generic desktop / keyboard
        RAWINPUTDEVICE(0x0001, 0x0080, RIDEV_INPUTSINK, hwnd),  # generic desktop / system control
        RAWINPUTDEVICE(0x000C, 0x0001, RIDEV_INPUTSINK, hwnd),  # consumer
        RAWINPUTDEVICE(0xFF00, 0x0000, RIDEV_INPUTSINK | RIDEV_PAGEONLY, hwnd),  # vendor page (all usages)
    )
    user32.RegisterRawInputDevices.restype  = wintypes.BOOL
    user32.RegisterRawInputDevices.argtypes = [ctypes.POINTER(RAWINPUTDEVICE),
                                               wintypes.UINT, wintypes.UINT]
    ok = user32.RegisterRawInputDevices(rids, 4, ctypes.sizeof(RAWINPUTDEVICE))
    if not ok:
        print(f"RegisterRawInputDevices failed: {ctypes.get_last_error()}")
        return
    print(f"Registered for raw HID input. Listening for {seconds}s. Hold Fn for ~3s.\n", flush=True)

    msg = wintypes.MSG()
    t_end = time.time() + seconds
    user32.PeekMessageW.restype  = wintypes.BOOL
    user32.PeekMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    PM_REMOVE = 0x0001
    while time.time() < t_end:
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.001)
    print("done.")

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
