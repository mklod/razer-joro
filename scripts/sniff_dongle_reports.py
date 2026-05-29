"""
Open every readable PID_009C HID collection and print every report that
arrives. Used to figure out which dongle interface forwards which kind of
event (consumer/system/vendor/Fn-state) when Joro keys are pressed.

Press these keys with this running:
  - F8 / F9 (brightness keys) — should appear on consumer interface
  - Fn + Left / Fn + Right — should appear on a vendor interface (~12 byte report starting [0x05, 0x04, state])
  - Win+L (Lock) / Copilot — should appear as standard keyboard scancode reports (probably blocked by kbdhid exclusive open)
  - Mute / VolumeUp/Down — consumer reports

Usage:
    python sniff_dongle_reports.py [seconds=30]
"""
import hid, sys, time, threading

VID = 0x1532
PID = 0x009C


def reader(path_label, dev, stop_evt):
    while not stop_evt.is_set():
        try:
            data = dev.read(64, 50)  # 50ms timeout
        except Exception as e:
            print(f"[{path_label}] read error: {e}", flush=True)
            return
        if data:
            ts = time.strftime("%H:%M:%S")
            hexed = ' '.join(f"{b:02x}" for b in data)
            print(f"{ts}  [{path_label}]  ({len(data):2d}b) {hexed}", flush=True)


def main(seconds=30):
    devs = list(hid.enumerate(VID, PID))
    print(f"Found {len(devs)} PID_009C interfaces; opening readable ones...\n")
    threads = []
    handles = []
    stop_evt = threading.Event()
    for d in devs:
        path = d['path']
        path_str = path.decode('latin-1', errors='replace') if isinstance(path, bytes) else path
        # Build a short label like "MI_01_Col05"
        # Pull MI_xx and Col_yy from path
        import re
        m = re.search(r'MI_([0-9a-fA-F]+)(?:&Col_?([0-9a-fA-F]+))?', path_str, re.IGNORECASE)
        if m:
            label = f"MI{m.group(1)}" + (f".Col{m.group(2)}" if m.group(2) else "")
        else:
            label = path_str[-40:]
        try:
            h = hid.device()
            h.open_path(path)
            h.set_nonblocking(False)
            handles.append(h)
            t = threading.Thread(target=reader, args=(label, h, stop_evt), daemon=True)
            t.start()
            threads.append(t)
            print(f"  [+] {label} usage_page=0x{d['usage_page']:04x} usage=0x{d['usage']:04x}", flush=True)
        except Exception as e:
            print(f"  [-] {label}: {e}", flush=True)

    print(f"\n=== Sniffing for {seconds}s. Press the broken keys now. ===\n")
    time.sleep(seconds)
    stop_evt.set()
    print("\n=== Done. ===")
    for h in handles:
        try: h.close()
        except: pass


if __name__ == "__main__":
    secs = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(secs)
