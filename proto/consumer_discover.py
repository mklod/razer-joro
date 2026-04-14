"""Consumer HID discovery for Razer Joro.

Opens every Joro HID interface with a "Consumer Control" usage page and
logs every report received for a set window (default 25 s). Use this to
learn exactly what consumer usage codes Joro emits for its F-row media
keys (mute, vol-, vol+, play/pause, arrange-windows on F4, etc.) in
mm-primary mode.

Consumer Control interface signature:
  - usage_page = 0x000C  (Consumer Devices page)
  - usage      = 0x0001  (Consumer Control)

Joro may expose multiple such interfaces (USB composite device with
several HID collections). We open ALL of them and tag each report with
its interface number + collection so the caller can tell them apart.

Usage:
  python consumer_discover.py              # 25-second window
  python consumer_discover.py 60           # 60-second window
"""
import hid
import sys
import time

RAZER_VID = 0x1532
JORO_PID = 0x02CD

WINDOW_SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 25

# HID Consumer Control usage codes we're most likely to see from Joro
# (partial — Razer firmware may emit vendor-specific codes too).
CONSUMER_USAGE_NAMES = {
    0x00B5: "Scan Next Track",
    0x00B6: "Scan Previous Track",
    0x00B7: "Stop",
    0x00CD: "Play/Pause",
    0x00E2: "Mute",
    0x00E9: "Volume Up",
    0x00EA: "Volume Down",
    0x0192: "AL Calculator",
    0x0194: "AL Local Machine Browser",
    0x0196: "AL Internet Browser",
    0x01A1: "AL Network Chat Client",
    0x01A2: "AL Email Reader",
    0x01AE: "AL Keyboard Layout",
    0x01B7: "AL Screen Saver",
    0x0221: "AC Search",
    0x0223: "AC Home",
    0x0224: "AC Back",
    0x0225: "AC Forward",
    0x0226: "AC Stop",
    0x0227: "AC Refresh",
    0x022A: "AC Bookmarks",
    0x029D: "AC View Toggle",  # F4 arrange-windows candidate
    0x029F: "AC Task Management",  # F4 arrange-windows candidate
    0x02A0: "AC Window Management",  # F4 arrange-windows candidate
    0x02A2: "AC Screen Management",  # F4 arrange-windows candidate
}


def decode_usage(usage: int) -> str:
    name = CONSUMER_USAGE_NAMES.get(usage, "?")
    return f"0x{usage:04X} ({name})"


def open_consumer_interfaces():
    """Enumerate Joro interfaces and open every Consumer Control one."""
    devs = hid.enumerate(RAZER_VID, JORO_PID)
    opened = []
    print(f"Enumerating Joro HID interfaces ({len(devs)} found):")
    for d in devs:
        up = d["usage_page"]
        us = d["usage"]
        tag = f"iface={d['interface_number']} up=0x{up:04X} us=0x{us:04X}"
        # Consumer Control page 0x000C usage 0x0001 — or any other consumer
        # collection (system controls 0x0001/0x0080 too, in case F-keys emit
        # system codes instead).
        is_consumer = up == 0x000C and us == 0x0001
        is_system = up == 0x0001 and us == 0x0080
        marker = ""
        if is_consumer:
            marker = "  <-- CONSUMER (opening)"
        elif is_system:
            marker = "  <-- SYSTEM CONTROL (opening, may carry F4 arrange-windows)"
        print(f"  {tag}{marker}")
        if is_consumer or is_system:
            try:
                h = hid.device()
                h.open_path(d["path"])
                h.set_nonblocking(True)
                opened.append({
                    "handle": h,
                    "iface": d["interface_number"],
                    "usage_page": up,
                    "usage": us,
                    "path": d["path"],
                })
            except Exception as e:
                print(f"    open failed: {e}")
    return opened


def format_report(data: bytes) -> str:
    return " ".join(f"{b:02x}" for b in data)


def extract_consumer_usage(data: bytes) -> int | None:
    """Best-effort decode of a 2-byte little-endian consumer usage code
    from a typical HID consumer report. Most Razer keyboards emit reports
    that are `[report_id, usage_lo, usage_hi, ...]` where usage=0 on
    key-up. Returns None for key-up or unparseable shapes."""
    if len(data) < 3:
        return None
    # Report id then 2-byte usage
    usage = data[1] | (data[2] << 8)
    return usage if usage != 0 else None


def main():
    interfaces = open_consumer_interfaces()
    if not interfaces:
        print("\nNo consumer/system interfaces found to monitor.")
        print("Make sure Joro is connected via USB and no other process is")
        print("holding the HID interface exclusively (e.g. Razer Synapse).")
        return

    print(f"\n=== Listening on {len(interfaces)} interface(s) for {WINDOW_SECS}s ===")
    print("Press each F-row key (F1..F12) a couple times in mm-primary mode.")
    print("Include Mute / Vol+ / Vol- / Play-Pause if you want them too.")
    print("Also try F1/F2/F3 to see what BLE slot selectors emit (if anything).\n")

    seen = {}   # {(iface, usage): count}
    first_seen = {}
    start = time.monotonic()
    end = start + WINDOW_SECS

    while time.monotonic() < end:
        for ifinfo in interfaces:
            try:
                data = ifinfo["handle"].read(64, timeout_ms=0)
            except Exception as e:
                data = None
            if not data:
                continue
            ts = time.monotonic() - start
            iface = ifinfo["iface"]
            usage = extract_consumer_usage(bytes(data))
            raw_hex = format_report(bytes(data))
            print(f"[{ts:6.2f}s] iface={iface} report={raw_hex}")
            if usage is not None:
                key = (iface, usage)
                seen[key] = seen.get(key, 0) + 1
                if key not in first_seen:
                    first_seen[key] = ts
        time.sleep(0.001)

    print("\n=== Summary: unique usages observed ===")
    if not seen:
        print("(nothing — try again or check that keys actually emit consumer reports)")
    else:
        for (iface, usage), count in sorted(seen.items(), key=lambda kv: first_seen[kv[0]]):
            name = decode_usage(usage)
            t0 = first_seen[(iface, usage)]
            print(f"  iface={iface}  {name}  seen x{count}  first @ {t0:.2f}s")

    for ifinfo in interfaces:
        try:
            ifinfo["handle"].close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
