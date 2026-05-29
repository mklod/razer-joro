"""
Meter the Joro-on-dongle firmware sleep/wake timing.

Logs, on ONE monotonic clock (ms since start):
  HB   <hex>           — a heartbeat HID report (09 31 ...) on any readable
                          PID_009C collection. Present only while the
                          keyboard firmware is awake; stops when it sleeps.
  KEY  <dn|up> vk sc    — a WH_KEYBOARD_LL event (what the OS actually got).

Protocol the operator runs (instructions printed at start):
  For each of N cycles:
    1. Type continuously ~10 s.
    2. Hands OFF the keyboard ~90 s (let firmware sleep).
    3. Wake it: HOLD one key (e.g. 'a') down for ~3 s, then release.
    4. Brief pause, repeat.

Afterwards run with `analyze <logfile>` to print per-cycle:
  - awake-tail   = last HB  − last KEY before the idle gap
  - sleep-gap    = first HB − last HB across the idle gap
  - wake-latency = first KEY − first HB after the gap   (the annoying delay:
                   radio/firmware woke on your press, this is the extra
                   time before keystrokes actually flow to the OS)
  - dropped      = KEY-down events expected vs seen at wake (hold-key
                   autorepeat: count the gap before repeats begin)
"""
import sys, time, threading, ctypes, re
from ctypes import wintypes

# ── shared clock ───────────────────────────────────────────────────────────
T0 = time.perf_counter()
def ms() -> float:
    return (time.perf_counter() - T0) * 1000.0

LOGF = None
_lock = threading.Lock()
def log(kind: str, detail: str):
    line = f"{ms():10.1f}  {kind:4s} {detail}"
    with _lock:
        print(line, flush=True)
        if LOGF:
            LOGF.write(line + "\n"); LOGF.flush()

# ── heartbeat reader (hidapi) ──────────────────────────────────────────────
def hb_reader(seconds: int):
    import hid
    VID, PID = 0x1532, 0x009C
    handles = []
    for d in hid.enumerate(VID, PID):
        try:
            h = hid.device(); h.open_path(d['path']); h.set_nonblocking(True)
            handles.append((d['path'][-30:].decode('latin1','replace') if isinstance(d['path'],bytes) else str(d['path'])[-30:], h))
        except Exception:
            pass
    log("INFO", f"opened {len(handles)} PID_009C collections for heartbeat watch")
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        for label, h in handles:
            try:
                data = h.read(16)
            except Exception:
                data = None
            if data and len(data) >= 2 and data[0] == 0x09 and data[1] == 0x31:
                hexed = ' '.join(f'{b:02x}' for b in data[:8])
                log("HB", f"[{hexed}] raw_batt=0x{data[2]:02x}")
        time.sleep(0.01)
    log("INFO", "heartbeat reader stopped")

# ── WH_KEYBOARD_LL hook ────────────────────────────────────────────────────
WH_KEYBOARD_LL = 13
WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP = 0x100, 0x101, 0x104, 0x105
user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
LLProc = ctypes.WINFUNCTYPE(wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

class KBD(ctypes.Structure):
    _fields_ = [('vk', wintypes.DWORD), ('sc', wintypes.DWORD),
                ('flags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('extra', ctypes.c_void_p)]

def _proc(nCode, wParam, lParam):
    if nCode >= 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBD))[0]
        if wParam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            log("KEY", f"dn vk=0x{kb.vk:02X} sc=0x{kb.sc:02X}")
        elif wParam in (WM_KEYUP, WM_SYSKEYUP):
            log("KEY", f"up vk=0x{kb.vk:02X} sc=0x{kb.sc:02X}")
    return user32.CallNextHookEx(None, nCode, wParam, lParam)

def key_hook(seconds: int):
    proc = LLProc(_proc)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LLProc, wintypes.HMODULE, wintypes.DWORD]
    hk = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, kernel32.GetModuleHandleW(None), 0)
    if not hk:
        log("INFO", f"SetWindowsHookEx FAILED err={ctypes.get_last_error()}"); return
    log("INFO", "WH_KEYBOARD_LL installed")
    msg = wintypes.MSG()
    t_end = time.perf_counter() + seconds
    while time.perf_counter() < t_end:
        if user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        else:
            time.sleep(0.002)
    user32.UnhookWindowsHookEx(hk)
    log("INFO", "key hook stopped")

# ── analysis ───────────────────────────────────────────────────────────────
def analyze(path):
    rows = []
    for ln in open(path):
        m = re.match(r'\s*([\d.]+)\s+(HB|KEY|INFO)\s+(.*)', ln)
        if m:
            rows.append((float(m.group(1)), m.group(2), m.group(3).strip()))
    hbs  = [t for (t,k,_) in rows if k == 'HB']
    keys = [(t,d) for (t,k,d) in rows if k == 'KEY' and d.startswith('dn')]
    if not hbs:
        print("No heartbeats captured — keyboard never heartbeat (wrong collection?) "
              "or stayed asleep the whole time."); return
    # Find idle gaps in the heartbeat stream (> 8s with no HB = slept).
    print(f"{len(hbs)} heartbeats, {len(keys)} key-downs over "
          f"{rows[-1][0]/1000:.1f}s\n")
    GAP = 8000.0  # ms; heartbeat normally every ~2-5s, so 8s = slept
    cyc = 0
    for i in range(1, len(hbs)):
        gap = hbs[i] - hbs[i-1]
        if gap < GAP:
            continue
        cyc += 1
        last_hb_before = hbs[i-1]
        first_hb_after = hbs[i]
        # last key-down before the keyboard went quiet
        kb_before = [t for (t,_) in keys if t <= last_hb_before]
        last_key_before = kb_before[-1] if kb_before else None
        # first key-down after wake
        ka_after = [t for (t,_) in keys if t >= first_hb_after]
        first_key_after = ka_after[0] if ka_after else None
        print(f"── sleep cycle {cyc} ──")
        if last_key_before is not None:
            print(f"  awake-tail   : {last_hb_before - last_key_before:8.0f} ms "
                  f"(input stop → last heartbeat; firmware idle-before-sleep timeout ≳ this)")
        print(f"  sleep-gap    : {first_hb_after - last_hb_before:8.0f} ms "
              f"(no heartbeats — confirmed asleep)")
        if first_key_after is not None:
            print(f"  wake-latency : {first_key_after - first_hb_after:8.0f} ms "
                  f"(first heartbeat-resume → first keystroke the OS received)")
        # dropped keystrokes at wake: time from first_hb_after to first_key_after
        # in a hold-key test is the input the user lost.
        print()

# ── main ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if len(sys.argv) >= 3 and sys.argv[1] == "analyze":
        analyze(sys.argv[2]); sys.exit(0)
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 360
    LOGF = open(r"L:\PROJECTS\razer-joro\captures\dongle_sleep_meter.log", "w")
    print(f"Metering for {secs}s. Operator protocol (repeat ~3 cycles):")
    print("  1. Type continuously ~10s")
    print("  2. HANDS OFF ~90s (let it sleep)")
    print("  3. Wake: HOLD 'a' down ~3s then release")
    print("  4. short pause, repeat")
    print("Shared clock running. Begin.\n")
    t1 = threading.Thread(target=hb_reader, args=(secs,), daemon=True)
    t1.start()
    key_hook(secs)   # runs on main thread (needs message pump)
    t1.join(timeout=2)
    LOGF.close()
    print("\nDone. Analyze with:")
    print(r"  python meter_dongle_sleep.py analyze L:\PROJECTS\razer-joro\captures\dongle_sleep_meter.log")
