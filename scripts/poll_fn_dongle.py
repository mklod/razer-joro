"""
Poll report ID 0x05 (Fn-state on BLE Joro: [0x05, 0x04, state, ...])
on every readable PID_009C HID collection. If the dongle exposes Fn
on a solicited (HidD_GetInputReport) report rather than pushed, this
will see it where the existing hidapi read() does not.

Hold Fn while running. Output prints the report buffer for every
collection where the report changes.
"""
import hid, sys, time

VID = 0x1532
PID = 0x009C

def main(seconds=20):
    devs = list(hid.enumerate(VID, PID))
    handles = []
    for d in devs:
        path = d['path']
        try:
            h = hid.device()
            h.open_path(path)
            label = f"MI_{d['interface_number']:02x}_up{d['usage_page']:04x}_u{d['usage']:04x}"
            handles.append((label, h))
            print(f"  [+] {label} path-tail={path[-50:]}")
        except Exception as e:
            pass
    print(f"\nPolling {len(handles)} interfaces for report 0x05 every 50ms for {seconds}s.\n"
          f"Hold Fn for ~3s, release ~3s, repeat.\n", flush=True)
    last = {}
    t_end = time.time() + seconds
    while time.time() < t_end:
        for label, h in handles:
            try:
                data = h.get_input_report(5, 16)  # [report_id, ...15 data bytes]
                if not data:
                    continue
                tup = tuple(data)
                if last.get(label) != tup:
                    last[label] = tup
                    ts = time.strftime("%H:%M:%S")
                    hexed = ' '.join(f"{b:02x}" for b in data)
                    print(f"{ts}  {label}  ({len(data)}B) {hexed}", flush=True)
            except Exception as e:
                # First failure prints once
                if label not in last:
                    last[label] = ('err',)
                    print(f"   {label}: {e}", flush=True)
        time.sleep(0.05)
    print("done.")
    for _, h in handles:
        try: h.close()
        except: pass

if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 20)
