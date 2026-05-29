"""
Direct ctypes call to HidD_GetInputReport on every PID_009C HID
collection, for report IDs 1..15. If any returned buffer changes
between Fn-held and Fn-released, we've found the channel.

Hold Fn for 3s, release 3s, hold 3s, release.
"""
import ctypes, sys, time
from ctypes import wintypes
import hid  # Just for enumeration

VID = 0x1532
PID = 0x009C

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
hidlib   = ctypes.WinDLL('hid',     use_last_error=True)

# CreateFileW
GENERIC_READ  = 0x80000000
GENERIC_WRITE = 0x40000000
FILE_SHARE_READ  = 1
FILE_SHARE_WRITE = 2
OPEN_EXISTING = 3
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

CreateFileW = kernel32.CreateFileW
CreateFileW.restype  = wintypes.HANDLE
CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                        wintypes.HANDLE]
CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]

HidD_GetInputReport = hidlib.HidD_GetInputReport
HidD_GetInputReport.restype  = wintypes.BOOLEAN
HidD_GetInputReport.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]

def open_dongle(path_str):
    h = CreateFileW(path_str,
                    GENERIC_READ | GENERIC_WRITE,
                    FILE_SHARE_READ | FILE_SHARE_WRITE,
                    None, OPEN_EXISTING, 0, None)
    if h == INVALID_HANDLE_VALUE or h == 0:
        return None
    return h

def try_report(h, report_id, length):
    buf = (ctypes.c_ubyte * length)()
    buf[0] = report_id
    ok = HidD_GetInputReport(h, buf, length)
    if not ok:
        return None
    return bytes(buf)

def main(seconds=20):
    devs = list(hid.enumerate(VID, PID))
    handles = []
    for d in devs:
        path = d['path']
        path_str = path.decode('latin-1', errors='replace') if isinstance(path, bytes) else path
        h = open_dongle(path_str)
        if not h:
            continue
        label = f"MI_{d['interface_number']:02x}_up{d['usage_page']:04x}_u{d['usage']:04x}_{d['path'][-12:].decode('latin-1', errors='replace')}"
        handles.append((label, h))
        print(f"  [+] opened {label}")

    print(f"\nPolling {len(handles)} interfaces × report IDs 1..15 every 100ms for {seconds}s.")
    print("Hold Fn for ~3s, release ~3s, repeat.\n", flush=True)

    last_state = {}
    t_end = time.time() + seconds
    cycle = 0
    while time.time() < t_end:
        cycle += 1
        for label, h in handles:
            for rid in range(1, 16):
                key = (label, rid)
                buf = try_report(h, rid, 32)
                if buf is None:
                    if key not in last_state:
                        last_state[key] = ('failed',)
                    continue
                tup = tuple(buf)
                if last_state.get(key) != tup:
                    last_state[key] = tup
                    ts = time.strftime("%H:%M:%S")
                    hexed = ' '.join(f"{b:02x}" for b in buf)
                    print(f"{ts} CHG  {label}  rid={rid}  {hexed}", flush=True)
        time.sleep(0.1)

    # Print summary
    successes = [(k, v) for k, v in last_state.items() if v != ('failed',)]
    print(f"\nTotal cycles: {cycle}, total (interface,rid) successes: {len(successes)}")
    for h in [hh for _, hh in handles]:
        try: CloseHandle(h)
        except: pass

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
