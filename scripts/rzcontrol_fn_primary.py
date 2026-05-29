#!/usr/bin/env python3
"""Synapse-parity Fn-primary for Joro BLE — single overlapped handle.

This replicates Synapse's IOCTL flow exactly:
  1. Open ONE handle with FILE_FLAG_OVERLAPPED.
  2. Post a 0x88883018 read first (queues, returns STATUS_PENDING).
  3. EnableInputHook(1) on the same handle.
  4. EnableInputNotify(1).
  5. SetInputHook for each scancode.
  6. Reader thread polls 0x88883018 completions and re-injects via
     0x88883020 cmd=1.

All IOCTLs use OVERLAPPED structures because the handle is overlapped.
Writes wait synchronously via WaitForSingleObject. Reads wait async.
"""
import ctypes, ctypes.wintypes as wt, threading, time, sys

# ── IOCTLs ────────────────────────────────────────────────────────────
IOCTL_READ_EVENT          = 0x88883018
IOCTL_CMD                 = 0x88883020
IOCTL_SET_INPUT_HOOK      = 0x88883024
IOCTL_ENABLE_INPUT_HOOK   = 0x88883034
IOCTL_ENABLE_INPUT_NOTIFY = 0x88883038

CMD_INJECT_SCANCODE = 0x01
CMD_CONSUMER_FILTER = 0x0a

FN_PRIMARY_SCANCODES = {
    0x3e: 'F4',
    0x3f: 'F5', 0x40: 'F6', 0x41: 'F7', 0x42: 'F8',
    0x43: 'F9', 0x44: 'F10', 0x57: 'F11', 0x58: 'F12',
}

CONSUMER_FILTER_USAGES = [0x0070, 0x006f]  # BrightnessDown / BrightnessUp

FILE_FLAG_OVERLAPPED = 0x40000000
ERROR_IO_PENDING = 997
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258

# ── Win32 FFI ─────────────────────────────────────────────────────────
k32 = ctypes.WinDLL('kernel32', use_last_error=True)
setupapi = ctypes.WinDLL('setupapi', use_last_error=True)

class OVERLAPPED(ctypes.Structure):
    _fields_ = [
        ('Internal', ctypes.c_void_p),
        ('InternalHigh', ctypes.c_void_p),
        ('Offset', wt.DWORD),
        ('OffsetHigh', wt.DWORD),
        ('hEvent', wt.HANDLE),
    ]

class G(ctypes.Structure):
    _fields_ = [('a', ctypes.c_uint32), ('b', ctypes.c_uint16),
                ('c', ctypes.c_uint16), ('d', ctypes.c_ubyte * 8)]
RZGUID = G(0xe3be005d, 0xd130, 0x4910,
           (ctypes.c_ubyte * 8)(0x88, 0xff, 0x09, 0xae, 0x02, 0xf6, 0x80, 0xe9))

class SPDID(ctypes.Structure):
    _fields_ = [('cbSize', wt.DWORD), ('g', G), ('f', wt.DWORD), ('r', ctypes.c_void_p)]

setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(G), wt.LPCWSTR, wt.HWND, wt.DWORD]
setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(G), wt.DWORD, ctypes.POINTER(SPDID)]
setupapi.SetupDiEnumDeviceInterfaces.restype = wt.BOOL
setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SPDID), ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wt.BOOL

k32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p,
                            wt.DWORD, wt.DWORD, wt.HANDLE]
k32.CreateFileW.restype = wt.HANDLE
k32.DeviceIoControl.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD,
                                ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD),
                                ctypes.POINTER(OVERLAPPED)]
k32.DeviceIoControl.restype = wt.BOOL
k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]
k32.CreateEventW.restype = wt.HANDLE
k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
k32.WaitForSingleObject.restype = wt.DWORD
k32.GetOverlappedResult.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED),
                                    ctypes.POINTER(wt.DWORD), wt.BOOL]
k32.GetOverlappedResult.restype = wt.BOOL
k32.CancelIoEx.argtypes = [wt.HANDLE, ctypes.POINTER(OVERLAPPED)]
k32.CancelIoEx.restype = wt.BOOL
k32.ResetEvent.argtypes = [wt.HANDLE]
k32.ResetEvent.restype = wt.BOOL

def find_rzcontrol():
    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(RZGUID), None, None, 0x12)
    di = SPDID(); di.cbSize = ctypes.sizeof(SPDID)
    setupapi.SetupDiEnumDeviceInterfaces(h, None, ctypes.byref(RZGUID), 0, ctypes.byref(di))
    req = wt.DWORD(0)
    setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(di), None, 0, ctypes.byref(req), None)
    buf = (ctypes.c_ubyte * req.value)()
    ctypes.memmove(buf, ctypes.byref(wt.DWORD(8)), 4)
    setupapi.SetupDiGetDeviceInterfaceDetailW(h, ctypes.byref(di), buf, req.value, None, None)
    return ctypes.wstring_at(ctypes.addressof(buf) + 4)

def open_overlapped():
    p = find_rzcontrol()
    h = k32.CreateFileW(p, 0xC0000000, 3, None, 3, FILE_FLAG_OVERLAPPED, None)
    if h == 0 or h == -1:
        raise OSError(f"CreateFile: {ctypes.get_last_error()}")
    return h

# Per-call helpers. ALL operations on an overlapped handle MUST use
# OVERLAPPED. Writes wait sync (via WaitForSingleObject) before returning.

def overlapped_write(h, code, in_buf, in_len):
    """Issue a write IOCTL on an overlapped handle. Wait synchronously
    for completion via WaitForSingleObject. Returns (ok, last_error).
    Caller owns in_buf lifetime — keep it alive until this returns."""
    br = wt.DWORD(0)
    ev = k32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = ev
    in_ptr = ctypes.cast(in_buf, ctypes.c_void_p) if in_buf else None
    ok = k32.DeviceIoControl(h, code, in_ptr, in_len, None, 0, ctypes.byref(br), ctypes.byref(ov))
    err = ctypes.get_last_error()
    if not ok:
        if err == ERROR_IO_PENDING:
            w = k32.WaitForSingleObject(ev, 2000)
            if w == WAIT_OBJECT_0:
                k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(br), True)
                ok = True
            else:
                k32.CancelIoEx(h, ctypes.byref(ov))
    k32.CloseHandle(ev)
    return bool(ok), err

def enable_filter(h, on):
    val = bytes([1 if on else 0, 0, 0, 0])
    buf = (ctypes.c_uint8 * 4)(*val)
    overlapped_write(h, IOCTL_ENABLE_INPUT_HOOK, buf, 4)
    overlapped_write(h, IOCTL_ENABLE_INPUT_NOTIFY, buf, 4)

def set_hook(h, sc, active):
    buf = (ctypes.c_uint8 * 292)()
    buf[4] = 1 if active else 0
    buf[0x0a] = sc & 0xff
    buf[0x0b] = (sc >> 8) & 0xff
    return overlapped_write(h, IOCTL_SET_INPUT_HOOK, buf, 292)

def cmd_consumer(h, usage):
    buf = (ctypes.c_uint8 * 32)()
    buf[4] = CMD_CONSUMER_FILTER
    buf[0x08] = usage & 0xff
    buf[0x09] = (usage >> 8) & 0xff
    return overlapped_write(h, IOCTL_CMD, buf, 32)

def inject_scancode(h, sc, state):
    buf = (ctypes.c_uint8 * 32)()
    buf[4] = CMD_INJECT_SCANCODE
    buf[0x0a] = sc & 0xff
    buf[0x0b] = (sc >> 8) & 0xff
    buf[0x0c] = state & 0xff
    buf[0x0d] = (state >> 8) & 0xff
    return overlapped_write(h, IOCTL_CMD, buf, 32)

stop = threading.Event()

def reader_loop(h):
    """Persistent buffers because the kernel writes into them while the
    IRP is pending — they must outlive each posted read."""
    out = (ctypes.c_uint8 * 304)()
    ev = k32.CreateEventW(None, True, False, None)
    ov = OVERLAPPED()
    ov.hEvent = ev
    br = wt.DWORD(0)
    try:
        while not stop.is_set():
            for i in range(304):
                out[i] = 0
            k32.ResetEvent(ev)
            ok = k32.DeviceIoControl(h, IOCTL_READ_EVENT, None, 0, out, 304,
                                     ctypes.byref(br), ctypes.byref(ov))
            err = ctypes.get_last_error()
            if not ok and err != ERROR_IO_PENDING:
                print(f"[reader] post failed err={err}; sleeping", flush=True)
                time.sleep(0.1)
                continue
            # Wait, rechecking stop every 250ms
            while not stop.is_set():
                w = k32.WaitForSingleObject(ev, 250)
                if w == WAIT_OBJECT_0:
                    break
                if w != WAIT_TIMEOUT:
                    print(f"[reader] wait err w={w}", flush=True)
                    break
            if stop.is_set():
                k32.CancelIoEx(h, ctypes.byref(ov))
                k32.WaitForSingleObject(ev, 500)
                break
            ok2 = k32.GetOverlappedResult(h, ctypes.byref(ov), ctypes.byref(br), True)
            if not ok2:
                err2 = ctypes.get_last_error()
                print(f"[reader] result err={err2}", flush=True)
                continue
            # Event at offset 0x10
            ev_type = int.from_bytes(bytes(out[0x10:0x14]), 'little')
            sc      = int.from_bytes(bytes(out[0x16:0x18]), 'little')
            state   = int.from_bytes(bytes(out[0x18:0x1a]), 'little')
            name = FN_PRIMARY_SCANCODES.get(sc)
            if ev_type == 1 and name and state in (0, 1):
                print(f"[reader] {name} sc=0x{sc:02x} state={state} -> inject", flush=True)
                inject_scancode(h, sc, state)
    finally:
        k32.CloseHandle(ev)

def main():
    h = open_overlapped()
    print(f"Opened overlapped handle=0x{h:x}", flush=True)

    # Synapse's exact init order: post a read FIRST, then enable.
    # Spawn the reader thread to post the read for us.
    t = threading.Thread(target=reader_loop, args=(h,), daemon=True)
    t.start()
    # Give the reader 100ms to post its first read before we enable
    time.sleep(0.1)

    enable_filter(h, True)
    print("Filter enabled", flush=True)

    for sc, name in FN_PRIMARY_SCANCODES.items():
        ok, err = set_hook(h, sc, True)
        print(f"Hook {name} sc=0x{sc:02x}: ok={ok} err={err}", flush=True)

    for usage in CONSUMER_FILTER_USAGES:
        ok, err = cmd_consumer(h, usage)
        print(f"Consumer filter 0x{usage:04x}: ok={ok} err={err}", flush=True)

    print("\n*** Single overlapped handle. Press F5..F12 — should emit VK_Fx.", flush=True)
    print("*** Ctrl+C to stop.\n", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
        stop.set()
        for sc, _ in FN_PRIMARY_SCANCODES.items():
            set_hook(h, sc, False)
        # NOT calling enable_filter(h, False) — DisableInputHook is destructive.
        k32.CloseHandle(h)
        print("Done.", flush=True)

if __name__ == '__main__':
    main()
