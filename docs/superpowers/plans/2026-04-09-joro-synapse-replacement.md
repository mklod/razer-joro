# Joro Synapse Replacement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Razer Synapse with a lightweight Rust service for the Razer Joro keyboard — backlight control, key remapping, BLE sleep fix, systray UI.

**Architecture:** Single-process Rust binary with embedded webview (tao+wry). HID and BLE run on background threads. Config persisted as TOML. Python prototype phase first to reverse-engineer unknown protocol commands via USB sniffing.

**Tech Stack:** Python 3 + hidapi + bleak (prototype), Rust + hidapi-rs + btleplug + tao + wry (production), Wireshark + USBPcap (capture)

**Spec:** `docs/superpowers/specs/2026-04-09-joro-synapse-replacement-design.md`

---

## File Structure

### Phase 1: Python Prototype

```
proto/
├── requirements.txt          # hidapi, bleak
├── razer_packet.py           # Packet builder: 90-byte struct, CRC, serialize/deserialize
├── usb_transport.py          # Open device by VID/PID, send/receive feature reports
├── commands.py               # High-level: set_color(), set_brightness(), get_brightness()
├── enumerate.py              # List all Razer HID devices (find dongle PID, interface map)
├── sniff_decoder.py          # Parse Wireshark JSON/CSV export into readable command log
├── test_lighting.py          # Interactive: set color + brightness, verify on hardware
├── test_remap.py             # Interactive: send remap packets captured from sniffing
├── ble_explore.py            # BLE GATT service/characteristic enumeration with bleak
└── docs/
    └── captured_commands.md  # Log of all sniffed commands with decoded fields
```

### Phase 2+: Rust Production

```
joro/
├── Cargo.toml
├── build.rs                  # Embed HTML/CSS/JS assets at compile time
├── src/
│   ├── main.rs               # Entry point, startup sequence, arg parsing
│   ├── packet.rs             # RazerPacket struct, CRC calc, builder pattern
│   ├── transport/
│   │   ├── mod.rs            # RazerTransport trait definition
│   │   ├── usb.rs            # UsbTransport impl (hidapi-rs)
│   │   ├── ble.rs            # BleTransport impl (btleplug)
│   │   └── dongle.rs         # DongleTransport impl (hidapi-rs, different PID)
│   ├── device.rs             # DeviceManager: probe, monitor, reconnect, command dispatch
│   ├── config.rs             # TOML load/save, defaults, validation
│   ├── commands.rs           # set_color, set_brightness, set_remap, set_sleep_config
│   ├── keymap.rs             # HID usage table: key name ↔ usage ID mapping
│   ├── ui/
│   │   ├── mod.rs            # Systray setup, webview creation, IPC handlers
│   │   └── assets/
│   │       ├── index.html    # Settings panel layout
│   │       ├── style.css     # Styling
│   │       └── app.js        # UI logic, IPC calls to Rust backend
│   └── cli.rs                # CLI subcommands for testing without UI
├── tests/
│   ├── packet_test.rs        # Packet construction + CRC correctness
│   ├── config_test.rs        # Config load/save/defaults
│   └── mock_transport.rs     # Mock transport for integration tests
└── assets/
    └── icon.ico              # Systray icon base (recolored at runtime)
```

---

## Phase 1: Python Prototype (RE & Validation)

> **Note:** Phase 1 is exploratory/RE work. Tasks involve building tools, sniffing USB traffic, and validating commands against real hardware. TDD is not applicable here — the keyboard is the test oracle. Each task ends with a manual hardware verification step.

---

### Task 1: Project Setup & Python Environment

**Files:**
- Create: `proto/requirements.txt`
- Create: `proto/razer_packet.py`

- [ ] **Step 1: Create proto directory and requirements**

```
proto/requirements.txt
```

```txt
hidapi>=0.14.0
bleak>=0.21.0
```

- [ ] **Step 2: Create virtual environment and install deps**

Run:
```bash
cd L:/PROJECTS/razer-joro
python -m venv proto/.venv
proto/.venv/Scripts/pip install -r proto/requirements.txt
```

Expected: packages install without error.

- [ ] **Step 3: Implement RazerPacket builder**

```python
# proto/razer_packet.py
# Last modified: <timestamp>
"""
Razer HID packet builder/parser.

90-byte packet structure:
  [0x00] report_id      = 0x00
  [0x01] status          = 0x00 (new), 0x02 (ok), 0x03 (error)
  [0x02] transaction_id  = 0x1F (Joro)
  [0x03] data_size_hi
  [0x04] data_size_lo
  [0x05] command_class
  [0x06] command_id
  [0x07..0x57] arguments (80 bytes)
  [0x58] crc             = XOR of bytes [0x02..0x57]
  [0x59] reserved        = 0x00
"""

PACKET_SIZE = 90
TRANSACTION_ID = 0x1F


def _crc(buf: bytes) -> int:
    """XOR of bytes 2 through 87 (indices 0x02..0x57)."""
    result = 0
    for b in buf[2:88]:
        result ^= b
    return result


def build_packet(command_class: int, command_id: int, data_size: int, args: bytes = b"") -> bytes:
    """Build a 90-byte Razer HID packet."""
    buf = bytearray(PACKET_SIZE)
    buf[0x00] = 0x00  # report_id
    buf[0x01] = 0x00  # status: new command
    buf[0x02] = TRANSACTION_ID
    buf[0x03] = (data_size >> 8) & 0xFF
    buf[0x04] = data_size & 0xFF
    buf[0x05] = command_class
    buf[0x06] = command_id
    for i, b in enumerate(args[:80]):
        buf[0x07 + i] = b
    buf[0x58] = _crc(buf)
    buf[0x59] = 0x00
    return bytes(buf)


def parse_packet(buf: bytes) -> dict:
    """Parse a 90-byte Razer HID packet into fields."""
    if len(buf) < PACKET_SIZE:
        raise ValueError(f"Packet too short: {len(buf)} bytes, expected {PACKET_SIZE}")
    data_size = (buf[0x03] << 8) | buf[0x04]
    return {
        "report_id": buf[0x00],
        "status": buf[0x01],
        "transaction_id": buf[0x02],
        "data_size": data_size,
        "command_class": buf[0x05],
        "command_id": buf[0x06],
        "args": buf[0x07:0x07 + data_size],
        "crc": buf[0x58],
        "crc_valid": buf[0x58] == _crc(buf),
    }


def format_packet(buf: bytes) -> str:
    """Pretty-print a packet for debugging."""
    p = parse_packet(buf)
    args_hex = " ".join(f"{b:02X}" for b in p["args"])
    return (
        f"status=0x{p['status']:02X} txn=0x{p['transaction_id']:02X} "
        f"class=0x{p['command_class']:02X} cmd=0x{p['command_id']:02X} "
        f"size={p['data_size']} crc_ok={p['crc_valid']}\n"
        f"  args: {args_hex}"
    )
```

- [ ] **Step 4: Verify packet builder with a quick sanity check**

Run:
```bash
cd L:/PROJECTS/razer-joro
proto/.venv/Scripts/python -c "
from proto.razer_packet import build_packet, parse_packet
pkt = build_packet(0x0F, 0x02, 3, bytes([0xFF, 0x00, 0x00]))
p = parse_packet(pkt)
assert p['command_class'] == 0x0F
assert p['command_id'] == 0x02
assert p['args'] == bytes([0xFF, 0x00, 0x00])
assert p['crc_valid']
print('Packet builder OK')
print(f'CRC: 0x{p[\"crc\"]:02X}')
"
```

Expected: `Packet builder OK` with a valid CRC.

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git init
git add proto/requirements.txt proto/razer_packet.py
git commit -m "feat: python prototype - packet builder with CRC"
```

---

### Task 2: USB Device Enumeration

**Files:**
- Create: `proto/enumerate.py`

- [ ] **Step 1: Write enumeration script**

```python
# proto/enumerate.py
# Last modified: <timestamp>
"""
Enumerate all Razer HID devices.

Lists every HID interface for VID 0x1532 — helps identify:
- Joro wired PID (0x02CD)
- 2.4GHz dongle PID (unknown)
- Which interface index to use for control transfers
"""

import hid

RAZER_VID = 0x1532
JORO_PID = 0x02CD


def enumerate_razer():
    """List all Razer HID devices with full interface details."""
    devices = hid.enumerate(RAZER_VID, 0)
    if not devices:
        print("No Razer HID devices found.")
        return []

    print(f"Found {len(devices)} Razer HID interface(s):\n")
    for d in devices:
        print(f"  Product:   {d['product_string']}")
        print(f"  VID/PID:   {d['vendor_id']:04X}:{d['product_id']:04X}")
        print(f"  Path:      {d['path']}")
        print(f"  Interface: {d['interface_number']}")
        print(f"  Usage:     page=0x{d['usage_page']:04X} usage=0x{d['usage']:04X}")
        print()
    return devices


def find_joro_control_interface():
    """Find the Joro control interface (usage_page 0x0001, usage 0x0006 — keyboard).

    The control interface for Razer command packets is typically interface 0
    with usage_page 0x0001. If multiple interfaces exist, we need the one
    that accepts feature reports (report_id 0x00, 90 bytes).
    """
    devices = hid.enumerate(RAZER_VID, JORO_PID)
    if not devices:
        print("Joro not found. Is it connected via USB?")
        return None

    print(f"Joro interfaces ({len(devices)}):\n")
    for d in devices:
        print(f"  iface={d['interface_number']} usage_page=0x{d['usage_page']:04X} "
              f"usage=0x{d['usage']:04X} path={d['path']}")

    # Try interface 0 first, fall back to iterating
    for d in devices:
        if d["interface_number"] == 0:
            print(f"\nRecommended control interface: iface=0, path={d['path']}")
            return d
    # If no iface 0, return first
    print(f"\nNo iface 0 found, using first: path={devices[0]['path']}")
    return devices[0]


if __name__ == "__main__":
    print("=== All Razer devices ===\n")
    enumerate_razer()
    print("\n=== Joro control interface ===\n")
    find_joro_control_interface()
```

- [ ] **Step 2: Run enumeration with Joro connected via USB**

Run:
```bash
cd L:/PROJECTS/razer-joro
proto/.venv/Scripts/python proto/enumerate.py
```

Expected: lists Joro interfaces with PID `02CD`. Note which interface number and usage_page work for control. If 2.4GHz dongle is plugged in, note its PID.

**Record the output** — interface numbers and paths will be needed for the transport layer.

- [ ] **Step 3: Commit**

```bash
git add proto/enumerate.py
git commit -m "feat: HID device enumeration for Joro and Razer dongles"
```

---

### Task 3: USB Transport — Send & Receive

**Files:**
- Create: `proto/usb_transport.py`

- [ ] **Step 1: Implement USB transport**

```python
# proto/usb_transport.py
# Last modified: <timestamp>
"""
USB HID transport for Razer Joro.

Sends and receives 90-byte feature reports via hidapi.
"""

import hid
import time
from razer_packet import PACKET_SIZE, RAZER_VID, JORO_PID if False else None

# Re-import cleanly
RAZER_VID = 0x1532
JORO_PID = 0x02CD


class UsbTransport:
    def __init__(self, device_path: bytes | None = None):
        self.device = hid.device()
        self._path = device_path

    def open(self, device_path: bytes | None = None):
        """Open the Joro HID device.

        Args:
            device_path: Specific HID path from enumeration. If None, tries
                         to open by VID/PID (uses first matching interface).
        """
        path = device_path or self._path
        if path:
            self.device.open_path(path)
        else:
            self.device.open(RAZER_VID, JORO_PID)
        print(f"Opened: {self.device.get_product_string()}")

    def close(self):
        self.device.close()

    def send_packet(self, packet: bytes) -> bytes:
        """Send a 90-byte packet as feature report, read response.

        Returns the 90-byte response packet.
        """
        # Feature report: prepend report_id 0x00
        # hidapi send_feature_report expects [report_id, ...data]
        report = bytes([0x00]) + packet[1:]  # packet[0] is already report_id
        written = self.device.send_feature_report(report)
        if written < 0:
            raise IOError(f"send_feature_report failed: {self.device.error()}")

        # Small delay for device to process
        time.sleep(0.02)

        # Read feature report response
        response = self.device.get_feature_report(0x00, PACKET_SIZE)
        if not response:
            raise IOError(f"get_feature_report failed: {self.device.error()}")

        return bytes(response)

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()
```

- [ ] **Step 2: Quick connectivity test (read-only)**

Run a safe read-only test — get brightness (class=0x0F, id=0x84):

```bash
cd L:/PROJECTS/razer-joro
proto/.venv/Scripts/python -c "
from proto.razer_packet import build_packet, parse_packet, format_packet
from proto.usb_transport import UsbTransport

pkt = build_packet(0x0F, 0x84, 0)  # get_brightness
with UsbTransport() as t:
    resp = t.send_packet(pkt)
    print(format_packet(resp))
"
```

Expected: response with `status=0x02` (OK) and brightness value in args. If this fails, check:
- Is Joro connected via USB?
- Is Synapse running (may hold the device)? Try killing Synapse first.
- Wrong interface? Use the path from `enumerate.py` instead.

- [ ] **Step 3: Commit**

```bash
git add proto/usb_transport.py
git commit -m "feat: USB HID transport - send/receive feature reports"
```

---

### Task 4: Lighting Commands

**Files:**
- Create: `proto/commands.py`
- Create: `proto/test_lighting.py`

- [ ] **Step 1: Implement lighting commands**

```python
# proto/commands.py
# Last modified: <timestamp>
"""
High-level Razer Joro commands.

Command reference from openrazer PR #2683 (Huntsman V3 Pro base class):
  Set static color:  class=0x0F, id=0x02, size=3, args=[R, G, B]  (note: may need variable_storage + led_id prefix, see step 3)
  Set brightness:    class=0x0F, id=0x04, size=1, args=[0-255]
  Get brightness:    class=0x0F, id=0x84, size=0

Commands discovered via sniffing will be added here.
"""

from razer_packet import build_packet, parse_packet


def set_brightness(transport, brightness: int) -> dict:
    """Set backlight brightness (0-255)."""
    pkt = build_packet(0x0F, 0x04, 1, bytes([brightness & 0xFF]))
    resp = transport.send_packet(pkt)
    return parse_packet(resp)


def get_brightness(transport) -> int:
    """Get current backlight brightness (0-255)."""
    pkt = build_packet(0x0F, 0x84, 0)
    resp = transport.send_packet(pkt)
    p = parse_packet(resp)
    if p["status"] != 0x02:
        raise IOError(f"get_brightness failed: status=0x{p['status']:02X}")
    return p["args"][0] if p["args"] else 0


def set_static_color(transport, r: int, g: int, b: int) -> dict:
    """Set static backlight color.

    Note: openrazer uses variable_storage=0x01 and led_id=0x00 as prefix
    args for some models. If this doesn't work, try:
      args = [0x01, 0x00, 0x00, 0x00, 0x00, R, G, B]
    with data_size=8 instead of 3.
    """
    pkt = build_packet(0x0F, 0x02, 3, bytes([r & 0xFF, g & 0xFF, b & 0xFF]))
    resp = transport.send_packet(pkt)
    return parse_packet(resp)
```

- [ ] **Step 2: Write interactive lighting test**

```python
# proto/test_lighting.py
# Last modified: <timestamp>
"""
Interactive lighting test — set color and brightness on Joro.

Usage:
  python test_lighting.py                    # default: orange, brightness 200
  python test_lighting.py FF0000             # red
  python test_lighting.py FF0000 128         # red, half brightness
"""

import sys
from usb_transport import UsbTransport
from commands import set_brightness, get_brightness, set_static_color
from razer_packet import format_packet


def main():
    color_hex = sys.argv[1] if len(sys.argv) > 1 else "FF6600"
    brightness = int(sys.argv[2]) if len(sys.argv) > 2 else 200

    r = int(color_hex[0:2], 16)
    g = int(color_hex[2:4], 16)
    b = int(color_hex[4:6], 16)

    with UsbTransport() as t:
        # Read current brightness
        cur = get_brightness(t)
        print(f"Current brightness: {cur}")

        # Set brightness
        print(f"\nSetting brightness to {brightness}...")
        resp = set_brightness(t, brightness)
        print(f"  status=0x{resp['status']:02X}")

        # Set color
        print(f"\nSetting color to #{color_hex} (R={r} G={g} B={b})...")
        resp = set_static_color(t, r, g, b)
        print(f"  status=0x{resp['status']:02X}")

        # Verify
        new_brightness = get_brightness(t)
        print(f"\nVerify brightness: {new_brightness}")
        print("\nCheck the keyboard — backlight should be the requested color.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run lighting test against hardware**

Run:
```bash
cd L:/PROJECTS/razer-joro/proto
../.venv/Scripts/python test_lighting.py FF0000 200
```

Expected: keyboard backlight changes to red at brightness 200. If it doesn't work:
1. Check response status — `0x03` means error, `0x02` means OK
2. The openrazer PR may use a different args layout with variable_storage/led_id prefix. Check the PR's `_set_static_effect()` method and adjust `commands.py` accordingly.
3. Try alternate args: `[0x01, 0x00, 0x00, 0x00, 0x00, R, G, B]` with data_size=8

**This step requires manual verification — look at the keyboard.**

- [ ] **Step 4: If needed, study openrazer PR for correct args layout**

If the simple 3-byte args don't work, clone the PR branch and check the exact packet format:

```bash
cd L:/PROJECTS/razer-joro
git clone --branch add-razer-joro-support --single-branch https://github.com/madbrainz/openrazer.git openrazer-ref
```

Look at:
- `openrazer-ref/driver/razercommon.h` — packet struct definition
- `openrazer-ref/driver/razerkbd_driver.c` — `razer_attr_write_mode_static()` function
- `openrazer-ref/daemon/openrazer_daemon/hardware/keyboards.py` — Joro class definition

Update `commands.py` with the correct args layout.

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add proto/commands.py proto/test_lighting.py
git commit -m "feat: lighting commands - set_color, set_brightness, get_brightness"
```

---

### Task 5: USB Sniff Session — Capture Synapse Traffic

**Files:**
- Create: `proto/sniff_decoder.py`
- Create: `proto/docs/captured_commands.md`

> **This task requires running Razer Synapse while capturing USB traffic.** It's a manual RE session with tooling support.

- [ ] **Step 1: Set up Wireshark + USBPcap**

1. Install USBPcap if not already present (https://desowin.org/usbpcap/)
2. Open Wireshark, select the USBPcap capture interface for the USB bus where Joro is connected
3. Apply capture filter: `usb.idVendor == 0x1532`
4. Start capture

- [ ] **Step 2: Capture Synapse operations**

With Wireshark capturing, perform these actions in Synapse (one at a time, with pauses between):

1. **Launch Synapse** — captures the full startup/init sequence (sleep config likely here)
2. **Change backlight color** — baseline to compare against openrazer commands
3. **Set a key remap** — the unknown we need most
4. **Change brightness** — another baseline
5. **Exit Synapse** — captures any shutdown/cleanup packets

Save the capture as `proto/captures/synapse_session.pcapng`.

- [ ] **Step 3: Write sniff decoder**

```python
# proto/sniff_decoder.py
# Last modified: <timestamp>
"""
Decode Razer HID packets from Wireshark JSON export.

Usage:
  1. In Wireshark: File > Export Packet Dissections > As JSON
     (filter to relevant packets first)
  2. python sniff_decoder.py captures/synapse_export.json
  
Alternatively, decode raw hex from clipboard:
  python sniff_decoder.py --hex "00001f000300..."
"""

import sys
import json
from razer_packet import parse_packet, format_packet, PACKET_SIZE


def decode_hex(hex_str: str) -> None:
    """Decode a single hex-encoded packet."""
    clean = hex_str.replace(" ", "").replace(":", "").replace("\n", "")
    raw = bytes.fromhex(clean)
    if len(raw) < PACKET_SIZE:
        raw = raw + b"\x00" * (PACKET_SIZE - len(raw))
    print(format_packet(raw[:PACKET_SIZE]))


def decode_json_export(path: str) -> None:
    """Decode packets from Wireshark JSON export.

    Looks for USB URB transfer payloads in the JSON structure.
    """
    with open(path) as f:
        packets = json.load(f)

    count = 0
    for i, pkt in enumerate(packets):
        layers = pkt.get("_source", {}).get("layers", {})

        # Look for USB HID data in various Wireshark JSON field names
        for field in ["usb.capdata", "usbhid.data", "HID Data"]:
            data_hex = None
            # Navigate nested structures
            if field in layers:
                data_hex = layers[field]
            elif "frame" in layers:
                frame_raw = layers.get("frame_raw", [""])[0] if "frame_raw" in layers else ""
                if len(frame_raw) >= PACKET_SIZE * 2:
                    data_hex = frame_raw[-PACKET_SIZE * 2:]

            if data_hex and isinstance(data_hex, str) and len(data_hex) >= PACKET_SIZE * 2:
                clean = data_hex.replace(":", "").replace(" ", "")
                raw = bytes.fromhex(clean[:PACKET_SIZE * 2])
                if raw[2] == 0x1F:  # Transaction ID matches Joro
                    count += 1
                    print(f"--- Packet {i} ---")
                    print(format_packet(raw))
                    print()

    print(f"Decoded {count} Joro packet(s).")


def main():
    if len(sys.argv) < 2:
        print("Usage: sniff_decoder.py <file.json>")
        print("       sniff_decoder.py --hex <hex_string>")
        sys.exit(1)

    if sys.argv[1] == "--hex":
        decode_hex(sys.argv[2])
    else:
        decode_json_export(sys.argv[1])


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Decode captured packets and document findings**

Run the decoder on the exported capture:
```bash
cd L:/PROJECTS/razer-joro
proto/.venv/Scripts/python proto/sniff_decoder.py proto/captures/synapse_export.json
```

Or decode individual packets from hex:
```bash
proto/.venv/Scripts/python proto/sniff_decoder.py --hex "00001f..."
```

Create the captured commands doc:

```markdown
# proto/docs/captured_commands.md
# Captured Razer Joro Commands

## From Synapse USB Sniff — <date>

### Startup Sequence
| # | class | id | size | args (hex) | Purpose |
|---|-------|-----|------|-----------|---------|
| 1 | 0x?? | 0x?? | ? | ... | (fill in from sniff) |

### Key Remap
| # | class | id | size | args (hex) | Purpose |
|---|-------|-----|------|-----------|---------|
| 1 | 0x?? | 0x?? | ? | ... | (fill in from sniff) |

### Sleep/Power Config
| # | class | id | size | args (hex) | Purpose |
|---|-------|-----|------|-----------|---------|
| 1 | 0x?? | 0x?? | ? | ... | (fill in from sniff) |

### Lighting (baseline comparison)
| # | class | id | size | args (hex) | Purpose |
|---|-------|-----|------|-----------|---------|
| 1 | 0x0F | 0x02 | ? | ... | set_static_color |

## Notes
- (observations, differences from openrazer, quirks)
```

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
mkdir -p proto/captures proto/docs
git add proto/sniff_decoder.py proto/docs/captured_commands.md
git commit -m "feat: sniff decoder and captured command documentation"
```

---

### Task 6: Validate Remap Commands

**Files:**
- Modify: `proto/commands.py` — add remap functions
- Create: `proto/test_remap.py`

> **Depends on Task 5 output.** The exact command_class, command_id, and args layout for remaps comes from the sniffing session. The code below uses placeholder class/id that MUST be replaced with actual values from `captured_commands.md`.

- [ ] **Step 1: Add remap command to commands.py**

Add to `proto/commands.py`:

```python
# --- Key Remapping ---
# Command class/id from sniff session (update these with real values)
REMAP_CLASS = 0x00  # REPLACE with actual value from captured_commands.md
REMAP_CMD_ID = 0x00  # REPLACE with actual value from captured_commands.md


def set_key_remap(transport, source_usage_id: int, target_usage_id: int) -> dict:
    """Remap a single key.

    Args:
        source_usage_id: HID usage ID of the physical key to remap
        target_usage_id: HID usage ID of the key it should produce

    The args layout comes from the Synapse sniff. Update this function
    to match the actual packet format observed.
    """
    # REPLACE args layout with actual format from sniff
    args = bytes([
        (source_usage_id >> 8) & 0xFF, source_usage_id & 0xFF,
        (target_usage_id >> 8) & 0xFF, target_usage_id & 0xFF,
    ])
    pkt = build_packet(REMAP_CLASS, REMAP_CMD_ID, len(args), args)
    resp = transport.send_packet(pkt)
    return parse_packet(resp)


def clear_remaps(transport) -> dict:
    """Clear all key remaps (reset to default).

    Command from sniff — may be same class with a different cmd_id,
    or same cmd_id with special args. Update after sniffing.
    """
    # REPLACE with actual clear command from sniff
    pkt = build_packet(REMAP_CLASS, REMAP_CMD_ID, 0)
    resp = transport.send_packet(pkt)
    return parse_packet(resp)
```

- [ ] **Step 2: Write interactive remap test**

```python
# proto/test_remap.py
# Last modified: <timestamp>
"""
Interactive key remap test.

Usage:
  python test_remap.py                        # test CapsLock -> LeftCtrl
  python test_remap.py <source_id> <target_id> # test specific HID usage IDs (hex)

After running, test the remap by pressing the remapped key.
"""

import sys
from usb_transport import UsbTransport
from commands import set_key_remap, clear_remaps


# Common HID usage IDs (keyboard page 0x07)
KEY_NAMES = {
    "CapsLock": 0x39,
    "LeftCtrl": 0xE0,
    "LeftShift": 0xE1,
    "LeftAlt": 0xE2,
    "LeftGui": 0xE3,
    "RightCtrl": 0xE4,
    "F12": 0x45,
    "ScrollLock": 0x47,
    "Pause": 0x48,
}


def main():
    if len(sys.argv) == 3:
        source = int(sys.argv[1], 16)
        target = int(sys.argv[2], 16)
    else:
        source = KEY_NAMES["CapsLock"]
        target = KEY_NAMES["LeftCtrl"]
        print(f"Default test: CapsLock (0x{source:02X}) -> LeftCtrl (0x{target:02X})")

    with UsbTransport() as t:
        print(f"\nSending remap: 0x{source:02X} -> 0x{target:02X}...")
        resp = set_key_remap(t, source, target)
        print(f"  status=0x{resp['status']:02X}")

        if resp["status"] == 0x02:
            print("\nRemap sent. Press the source key to verify it produces the target key.")
            input("Press Enter to clear remap and exit...")
            print("Clearing remaps...")
            resp = clear_remaps(t)
            print(f"  status=0x{resp['status']:02X}")
        else:
            print("\nRemap failed. Check captured_commands.md for correct class/id/args format.")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Test remap on hardware**

Run:
```bash
cd L:/PROJECTS/razer-joro/proto
../.venv/Scripts/python test_remap.py
```

Expected: CapsLock key produces Ctrl keypress. If status is `0x03` (error), the command format is wrong — go back to `captured_commands.md` and adjust.

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add proto/commands.py proto/test_remap.py
git commit -m "feat: key remap commands from sniff data"
```

---

### Task 7: BLE Exploration

**Files:**
- Create: `proto/ble_explore.py`

- [ ] **Step 1: Write BLE GATT explorer**

```python
# proto/ble_explore.py
# Last modified: <timestamp>
"""
BLE GATT exploration for Razer Joro.

Discovers BLE services/characteristics to identify:
- Which characteristic accepts Razer HID command packets
- Whether the packet format is identical to USB
- Connection parameters (for sleep fix investigation)

Usage:
  python ble_explore.py              # scan and connect to first Razer device
  python ble_explore.py <address>    # connect to specific BLE address
"""

import sys
import asyncio
from bleak import BleakScanner, BleakClient

RAZER_NAME_PREFIX = "Razer"


async def scan_for_joro():
    """Scan for Razer BLE devices."""
    print("Scanning for BLE devices (10 seconds)...")
    devices = await BleakScanner.discover(timeout=10.0)

    razer_devices = [d for d in devices if d.name and RAZER_NAME_PREFIX in d.name]

    if not razer_devices:
        print("No Razer BLE devices found. Is the Joro in BLE mode and advertising?")
        return None

    for d in razer_devices:
        print(f"  {d.name} — {d.address} (RSSI: {d.rssi})")

    return razer_devices[0]


async def explore_gatt(address: str):
    """Connect and enumerate all GATT services/characteristics."""
    print(f"\nConnecting to {address}...")
    async with BleakClient(address) as client:
        print(f"Connected: {client.is_connected}")
        print(f"MTU: {client.mtu_size}")

        print("\n=== GATT Services ===\n")
        for service in client.services:
            print(f"Service: {service.uuid} — {service.description}")
            for char in service.characteristics:
                props = ", ".join(char.properties)
                print(f"  Char: {char.uuid} [{props}]")
                if "read" in char.properties:
                    try:
                        val = await client.read_gatt_char(char.uuid)
                        print(f"    Value: {val.hex()}")
                    except Exception as e:
                        print(f"    Read error: {e}")
                for desc in char.descriptors:
                    try:
                        val = await client.read_gatt_descriptor(desc.handle)
                        print(f"    Desc {desc.uuid}: {val.hex()}")
                    except Exception:
                        pass
            print()


async def test_ble_command(address: str, char_uuid: str):
    """Try sending a get_brightness command over BLE.

    Args:
        char_uuid: The GATT characteristic UUID that accepts HID reports
                   (identified from explore_gatt output).
    """
    from razer_packet import build_packet, parse_packet, format_packet

    pkt = build_packet(0x0F, 0x84, 0)  # get_brightness

    print(f"\nConnecting to {address} for command test...")
    async with BleakClient(address) as client:
        print("Sending get_brightness...")
        await client.write_gatt_char(char_uuid, pkt, response=True)

        # Try reading response — may come via notification or read
        await asyncio.sleep(0.1)
        resp = await client.read_gatt_char(char_uuid)
        print(f"Response ({len(resp)} bytes):")
        if len(resp) >= 90:
            print(format_packet(resp))
        else:
            print(f"  Raw: {resp.hex()}")


async def main():
    if len(sys.argv) > 1:
        address = sys.argv[1]
    else:
        device = await scan_for_joro()
        if not device:
            return
        address = device.address

    await explore_gatt(address)

    print("\n--- Next steps ---")
    print("1. Identify which characteristic accepts 90-byte writes (look for 'write' property)")
    print("2. Run: python ble_explore.py <address> --test <char_uuid>")
    print("3. Compare response to USB transport response")

    if len(sys.argv) > 3 and sys.argv[2] == "--test":
        await test_ble_command(address, sys.argv[3])


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Run BLE scan with Joro in BLE mode**

Switch Joro to BLE mode, then:

```bash
cd L:/PROJECTS/razer-joro/proto
../.venv/Scripts/python ble_explore.py
```

Expected: finds Joro BLE device, enumerates GATT services. Record the characteristic UUID that supports write (this is where HID command packets go).

- [ ] **Step 3: Test BLE command (get_brightness)**

Once you know the writable characteristic UUID:

```bash
proto/.venv/Scripts/python proto/ble_explore.py <BLE_ADDRESS> --test <CHAR_UUID>
```

Expected: response matches USB transport response for the same command.

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add proto/ble_explore.py
git commit -m "feat: BLE GATT exploration and command testing"
```

---

### Task 8: Validate Sleep Config & Document All Commands

**Files:**
- Modify: `proto/commands.py` — add sleep config command
- Modify: `proto/docs/captured_commands.md` — finalize all findings

> **Depends on Task 5 sniff data.** The sleep config packet should be visible in the Synapse startup capture.

- [ ] **Step 1: Add sleep config command**

Add to `proto/commands.py`:

```python
# --- BLE Sleep Config ---
# From Synapse startup sniff (update with real values)
SLEEP_CLASS = 0x00  # REPLACE with actual value from captured_commands.md
SLEEP_CMD_ID = 0x00  # REPLACE with actual value from captured_commands.md


def set_sleep_config(transport, args: bytes) -> dict:
    """Send BLE sleep/power configuration.

    The exact args are from the Synapse startup sniff. This replays
    whatever Synapse sends to configure sleep behavior.

    Args:
        args: Raw argument bytes from the captured packet
    """
    pkt = build_packet(SLEEP_CLASS, SLEEP_CMD_ID, len(args), args)
    resp = transport.send_packet(pkt)
    return parse_packet(resp)
```

- [ ] **Step 2: Test sleep config over USB (while in BLE mode)**

The sleep config may need to be sent over USB before switching to BLE, or directly over BLE after connecting. Test both:

```bash
cd L:/PROJECTS/razer-joro/proto
# Over USB first
../.venv/Scripts/python -c "
from usb_transport import UsbTransport
from commands import set_sleep_config
with UsbTransport() as t:
    # REPLACE with actual args from sniff
    resp = set_sleep_config(t, bytes([0x00]))
    print(f'status=0x{resp[\"status\"]:02X}')
"
```

Then switch to BLE mode and check if the reconnect delay is improved.

- [ ] **Step 3: Finalize captured_commands.md**

Update `proto/docs/captured_commands.md` with all verified commands:
- Lighting (confirmed via Task 4)
- Remapping (confirmed via Task 6)
- Sleep config (this task)
- Any other startup/init commands observed

This document is the **protocol reference** for the Rust implementation.

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add proto/commands.py proto/docs/captured_commands.md
git commit -m "feat: sleep config command and finalized protocol reference"
```

---

### Task 9: Phase 1 Exit — Verify All Commands & Document

**Files:**
- Modify: `proto/docs/captured_commands.md` — final review

- [ ] **Step 1: Run full validation pass**

With Joro connected via USB, run each command and verify:

```bash
cd L:/PROJECTS/razer-joro/proto

echo "=== Brightness ==="
../.venv/Scripts/python -c "
from usb_transport import UsbTransport
from commands import get_brightness, set_brightness
with UsbTransport() as t:
    print(f'Current: {get_brightness(t)}')
    set_brightness(t, 128)
    print(f'After set: {get_brightness(t)}')
"

echo "=== Color ==="
../.venv/Scripts/python test_lighting.py 00FF00 200

echo "=== Remap ==="
../.venv/Scripts/python test_remap.py
```

- [ ] **Step 2: Verify Phase 1 exit criteria**

Check that all of the following are true:
- [ ] `set_brightness` works (observe keyboard brightness change)
- [ ] `get_brightness` returns correct value
- [ ] `set_static_color` works (observe keyboard color change)
- [ ] Key remap command identified and working (at least 1:1 swap verified)
- [ ] Sleep config command identified (even if BLE fix not fully verified yet)
- [ ] 2.4GHz dongle PID known (from enumeration)
- [ ] `captured_commands.md` has all command class/id/args documented
- [ ] BLE GATT characteristic for commands identified

Any items not verified become blockers for the corresponding Rust phase tasks.

- [ ] **Step 3: Commit final state**

```bash
cd L:/PROJECTS/razer-joro
git add -A proto/
git commit -m "feat: phase 1 complete - all protocol commands validated"
```

---

## Phase 2: Rust Core (Transport + Config)

> **Prerequisite:** Phase 1 complete. All command class/id/args known and documented in `proto/docs/captured_commands.md`.

---

### Task 10: Rust Project Scaffolding

**Files:**
- Create: `joro/Cargo.toml`
- Create: `joro/src/main.rs`

- [ ] **Step 1: Initialize Cargo project**

```bash
cd L:/PROJECTS/razer-joro
cargo init joro
```

- [ ] **Step 2: Set up Cargo.toml with dependencies**

Replace `joro/Cargo.toml`:

```toml
[package]
name = "joro"
version = "0.1.0"
edition = "2021"
description = "Lightweight Razer Joro keyboard driver — replaces Synapse"

[dependencies]
hidapi = "2.6"
toml = "0.8"
serde = { version = "1", features = ["derive"] }
serde_json = "1"
clap = { version = "4", features = ["derive"] }
anyhow = "1"

# Phase 3: uncomment when adding BLE
# btleplug = "0.11"
# tokio = { version = "1", features = ["full"] }

# Phase 4: uncomment when adding UI
# tao = "0.30"
# wry = "0.47"

[dev-dependencies]
```

- [ ] **Step 3: Create minimal main.rs**

```rust
// joro/src/main.rs
// Last modified: <timestamp>

use clap::Parser;

#[derive(Parser)]
#[command(name = "joro", about = "Razer Joro keyboard driver")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(clap::Subcommand)]
enum Commands {
    /// Set backlight color (hex RGB)
    SetColor { color: String },
    /// Set backlight brightness (0-255)
    SetBrightness { value: u8 },
    /// Get current brightness
    GetBrightness,
    /// Set a key remap
    Remap { source: String, target: String },
    /// Clear all remaps
    ClearRemaps,
}

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::SetColor { color } => {
            println!("set-color {color} (not yet implemented)");
        }
        Commands::SetBrightness { value } => {
            println!("set-brightness {value} (not yet implemented)");
        }
        Commands::GetBrightness => {
            println!("get-brightness (not yet implemented)");
        }
        Commands::Remap { source, target } => {
            println!("remap {source} -> {target} (not yet implemented)");
        }
        Commands::ClearRemaps => {
            println!("clear-remaps (not yet implemented)");
        }
    }
    Ok(())
}
```

- [ ] **Step 4: Verify it builds**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
cargo run -- --help
```

Expected: clean build, help text showing all subcommands.

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/
git commit -m "feat: rust project scaffold with CLI subcommands"
```

---

### Task 11: Packet Builder (Rust)

**Files:**
- Create: `joro/src/packet.rs`
- Create: `joro/tests/packet_test.rs`
- Modify: `joro/src/main.rs` — add `mod packet;`

- [ ] **Step 1: Write failing test**

```rust
// joro/tests/packet_test.rs
use joro::packet::{RazerPacket, TRANSACTION_ID};

#[test]
fn test_build_set_color_red() {
    let pkt = RazerPacket::new(0x0F, 0x02, &[0xFF, 0x00, 0x00]);
    let buf = pkt.to_bytes();

    assert_eq!(buf.len(), 90);
    assert_eq!(buf[0], 0x00); // report_id
    assert_eq!(buf[1], 0x00); // status
    assert_eq!(buf[2], TRANSACTION_ID); // 0x1F
    assert_eq!(buf[4], 3); // data_size lo
    assert_eq!(buf[5], 0x0F); // command_class
    assert_eq!(buf[6], 0x02); // command_id
    assert_eq!(buf[7], 0xFF); // R
    assert_eq!(buf[8], 0x00); // G
    assert_eq!(buf[9], 0x00); // B
}

#[test]
fn test_crc_valid() {
    let pkt = RazerPacket::new(0x0F, 0x04, &[200]);
    let buf = pkt.to_bytes();
    let parsed = RazerPacket::from_bytes(&buf).unwrap();
    assert!(parsed.crc_valid);
}

#[test]
fn test_roundtrip() {
    let original = RazerPacket::new(0x0F, 0x02, &[0xAA, 0xBB, 0xCC]);
    let buf = original.to_bytes();
    let parsed = RazerPacket::from_bytes(&buf).unwrap();

    assert_eq!(parsed.command_class, 0x0F);
    assert_eq!(parsed.command_id, 0x02);
    assert_eq!(&parsed.args[..3], &[0xAA, 0xBB, 0xCC]);
    assert!(parsed.crc_valid);
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo test --test packet_test
```

Expected: compilation error — `packet` module doesn't exist yet.

- [ ] **Step 3: Implement packet module**

```rust
// joro/src/packet.rs
// Last modified: <timestamp>

//! Razer HID 90-byte packet builder and parser.

pub const PACKET_SIZE: usize = 90;
pub const TRANSACTION_ID: u8 = 0x1F;

#[derive(Debug, Clone)]
pub struct RazerPacket {
    pub status: u8,
    pub transaction_id: u8,
    pub data_size: u16,
    pub command_class: u8,
    pub command_id: u8,
    pub args: Vec<u8>,
    pub crc: u8,
    pub crc_valid: bool,
}

impl RazerPacket {
    /// Build a new command packet.
    pub fn new(command_class: u8, command_id: u8, args: &[u8]) -> Self {
        Self {
            status: 0x00,
            transaction_id: TRANSACTION_ID,
            data_size: args.len() as u16,
            command_class,
            command_id,
            args: args.to_vec(),
            crc: 0, // computed in to_bytes()
            crc_valid: true,
        }
    }

    /// Serialize to 90-byte buffer.
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut buf = vec![0u8; PACKET_SIZE];
        buf[0] = 0x00; // report_id
        buf[1] = self.status;
        buf[2] = self.transaction_id;
        buf[3] = (self.data_size >> 8) as u8;
        buf[4] = (self.data_size & 0xFF) as u8;
        buf[5] = self.command_class;
        buf[6] = self.command_id;
        for (i, &b) in self.args.iter().take(80).enumerate() {
            buf[7 + i] = b;
        }
        buf[0x58] = crc(&buf);
        buf
    }

    /// Parse from 90-byte buffer.
    pub fn from_bytes(buf: &[u8]) -> anyhow::Result<Self> {
        if buf.len() < PACKET_SIZE {
            anyhow::bail!("packet too short: {} bytes", buf.len());
        }
        let data_size = ((buf[3] as u16) << 8) | buf[4] as u16;
        let expected_crc = crc(buf);
        Ok(Self {
            status: buf[1],
            transaction_id: buf[2],
            data_size,
            command_class: buf[5],
            command_id: buf[6],
            args: buf[7..7 + data_size as usize].to_vec(),
            crc: buf[0x58],
            crc_valid: buf[0x58] == expected_crc,
        })
    }
}

/// XOR of bytes 2..88.
fn crc(buf: &[u8]) -> u8 {
    buf[2..0x58].iter().fold(0u8, |acc, &b| acc ^ b)
}
```

Add to `joro/src/main.rs` at the top:

```rust
pub mod packet;
```

Also add `joro/src/lib.rs`:

```rust
// joro/src/lib.rs
pub mod packet;
```

- [ ] **Step 4: Run tests**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo test --test packet_test
```

Expected: all 3 tests pass.

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/packet.rs joro/src/lib.rs joro/tests/packet_test.rs
git commit -m "feat: RazerPacket builder with CRC - build and parse 90-byte packets"
```

---

### Task 12: Config Module

**Files:**
- Create: `joro/src/config.rs`
- Create: `joro/tests/config_test.rs`
- Modify: `joro/src/lib.rs` — add `pub mod config;`

- [ ] **Step 1: Write failing test**

```rust
// joro/tests/config_test.rs
use joro::config::JoroConfig;
use std::path::PathBuf;

#[test]
fn test_default_config() {
    let config = JoroConfig::default();
    assert_eq!(config.lighting.color, "#FFFFFF");
    assert_eq!(config.lighting.brightness, 255);
    assert!(config.remaps.is_empty());
}

#[test]
fn test_load_save_roundtrip() {
    let dir = std::env::temp_dir().join("joro_test_config");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("test.toml");

    let mut config = JoroConfig::default();
    config.lighting.color = "#FF6600".into();
    config.lighting.brightness = 200;
    config.remaps.insert("CapsLock".into(), "LeftCtrl".into());
    config.remaps.insert("ScrollLock".into(), "Ctrl+F12".into());

    config.save(&path).unwrap();
    let loaded = JoroConfig::load(&path).unwrap();

    assert_eq!(loaded.lighting.color, "#FF6600");
    assert_eq!(loaded.lighting.brightness, 200);
    assert_eq!(loaded.remaps.get("CapsLock").unwrap(), "LeftCtrl");
    assert_eq!(loaded.remaps.get("ScrollLock").unwrap(), "Ctrl+F12");

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn test_load_missing_creates_default() {
    let dir = std::env::temp_dir().join("joro_test_missing");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("nonexistent.toml");

    let config = JoroConfig::load_or_create(&path).unwrap();
    assert_eq!(config.lighting.color, "#FFFFFF");
    assert!(path.exists());

    std::fs::remove_dir_all(&dir).ok();
}

#[test]
fn test_parse_color() {
    let config = JoroConfig::default();
    let (r, g, b) = JoroConfig::parse_hex_color("#FF6600").unwrap();
    assert_eq!((r, g, b), (0xFF, 0x66, 0x00));
}
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo test --test config_test
```

Expected: compilation error.

- [ ] **Step 3: Implement config module**

```rust
// joro/src/config.rs
// Last modified: <timestamp>

//! TOML configuration for Joro.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JoroConfig {
    pub lighting: LightingConfig,
    #[serde(default)]
    pub sleep: SleepConfig,
    #[serde(default)]
    pub remaps: BTreeMap<String, String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LightingConfig {
    pub color: String,
    pub brightness: u8,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct SleepConfig {
    // Fields TBD from Phase 1 RE — placeholder struct for forward compatibility
}

impl Default for JoroConfig {
    fn default() -> Self {
        Self {
            lighting: LightingConfig {
                color: "#FFFFFF".into(),
                brightness: 255,
            },
            sleep: SleepConfig::default(),
            remaps: BTreeMap::new(),
        }
    }
}

impl JoroConfig {
    pub fn load(path: &Path) -> Result<Self> {
        let contents = std::fs::read_to_string(path)
            .with_context(|| format!("reading config from {}", path.display()))?;
        toml::from_str(&contents).context("parsing config TOML")
    }

    pub fn save(&self, path: &Path) -> Result<()> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        let contents = toml::to_string_pretty(self).context("serializing config")?;
        std::fs::write(path, contents).context("writing config file")
    }

    pub fn load_or_create(path: &Path) -> Result<Self> {
        if path.exists() {
            Self::load(path)
        } else {
            let config = Self::default();
            config.save(path)?;
            Ok(config)
        }
    }

    /// Parse "#RRGGBB" hex color to (r, g, b).
    pub fn parse_hex_color(hex: &str) -> Result<(u8, u8, u8)> {
        let hex = hex.trim_start_matches('#');
        if hex.len() != 6 {
            anyhow::bail!("invalid color hex: expected 6 chars, got {}", hex.len());
        }
        let r = u8::from_str_radix(&hex[0..2], 16)?;
        let g = u8::from_str_radix(&hex[2..4], 16)?;
        let b = u8::from_str_radix(&hex[4..6], 16)?;
        Ok((r, g, b))
    }
}
```

Add to `joro/src/lib.rs`:

```rust
pub mod config;
```

- [ ] **Step 4: Run tests**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo test --test config_test
```

Expected: all 4 tests pass.

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/config.rs joro/tests/config_test.rs joro/src/lib.rs
git commit -m "feat: TOML config module - load, save, defaults, hex color parsing"
```

---

### Task 13: USB Transport (Rust)

**Files:**
- Create: `joro/src/transport/mod.rs`
- Create: `joro/src/transport/usb.rs`
- Modify: `joro/src/lib.rs` — add `pub mod transport;`

- [ ] **Step 1: Define transport trait**

```rust
// joro/src/transport/mod.rs
// Last modified: <timestamp>

//! Transport abstraction for Razer HID communication.

pub mod usb;

use crate::packet::RazerPacket;
use anyhow::Result;

pub trait RazerTransport: Send {
    fn send(&self, packet: &RazerPacket) -> Result<RazerPacket>;
    fn is_connected(&self) -> bool;
}
```

- [ ] **Step 2: Implement USB transport**

```rust
// joro/src/transport/usb.rs
// Last modified: <timestamp>

//! USB HID transport via hidapi.

use crate::packet::{RazerPacket, PACKET_SIZE};
use super::RazerTransport;
use anyhow::{Context, Result};
use std::sync::Mutex;

const RAZER_VID: u16 = 0x1532;
const JORO_PID: u16 = 0x02CD;

pub struct UsbTransport {
    device: Mutex<hidapi::HidDevice>,
}

impl UsbTransport {
    /// Open Joro by VID/PID.
    pub fn open() -> Result<Self> {
        let api = hidapi::HidApi::new().context("initializing hidapi")?;
        let device = api.open(RAZER_VID, JORO_PID).context("opening Joro USB device")?;
        Ok(Self {
            device: Mutex::new(device),
        })
    }

    /// Open a specific HID path (from enumeration).
    pub fn open_path(path: &str) -> Result<Self> {
        let api = hidapi::HidApi::new().context("initializing hidapi")?;
        let device = api
            .open_path(std::ffi::CString::new(path)?.as_ref())
            .context("opening HID device by path")?;
        Ok(Self {
            device: Mutex::new(device),
        })
    }

    /// Enumerate all Joro HID interfaces.
    pub fn enumerate() -> Result<Vec<hidapi::DeviceInfo>> {
        let api = hidapi::HidApi::new()?;
        Ok(api
            .device_list()
            .filter(|d| d.vendor_id() == RAZER_VID && d.product_id() == JORO_PID)
            .cloned()
            .collect())
    }
}

impl RazerTransport for UsbTransport {
    fn send(&self, packet: &RazerPacket) -> Result<RazerPacket> {
        let dev = self.device.lock().unwrap();
        let buf = packet.to_bytes();

        // Send as feature report (report_id 0x00 prefix)
        let mut report = vec![0x00u8];
        report.extend_from_slice(&buf[1..]);
        dev.send_feature_report(&report)
            .context("send_feature_report")?;

        // Small delay for device processing
        std::thread::sleep(std::time::Duration::from_millis(20));

        // Read feature report response
        let mut resp_buf = vec![0u8; PACKET_SIZE + 1]; // +1 for report_id
        let n = dev
            .get_feature_report(&mut resp_buf)
            .context("get_feature_report")?;

        // Reconstruct 90-byte packet (hidapi may prepend report_id)
        let resp_start = if resp_buf[0] == 0x00 && n > PACKET_SIZE { 1 } else { 0 };
        let resp_bytes = &resp_buf[resp_start..resp_start + PACKET_SIZE];

        RazerPacket::from_bytes(resp_bytes)
    }

    fn is_connected(&self) -> bool {
        // hidapi doesn't have a direct "is connected" check.
        // Try a no-op read as a connectivity probe.
        let dev = self.device.lock().unwrap();
        let mut buf = vec![0u8; PACKET_SIZE + 1];
        dev.get_feature_report(&mut buf).is_ok()
    }
}
```

Add to `joro/src/lib.rs`:

```rust
pub mod transport;
```

- [ ] **Step 3: Verify it builds**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
```

Expected: clean build.

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/transport/
git commit -m "feat: USB transport - send/receive feature reports via hidapi-rs"
```

---

### Task 14: Commands Module & CLI Integration

**Files:**
- Create: `joro/src/commands.rs`
- Create: `joro/src/keymap.rs`
- Modify: `joro/src/main.rs` — wire CLI subcommands to real commands

- [ ] **Step 1: Implement keymap (HID usage table subset)**

```rust
// joro/src/keymap.rs
// Last modified: <timestamp>

//! HID usage table — key name to usage ID mapping.

use anyhow::{bail, Result};
use std::collections::HashMap;
use std::sync::LazyLock;

static KEY_MAP: LazyLock<HashMap<&'static str, u8>> = LazyLock::new(|| {
    let mut m = HashMap::new();
    // Letters
    for (i, c) in ('A'..='Z').enumerate() {
        let name: &'static str = Box::leak(format!("{c}").into_boxed_str());
        m.insert(name, 0x04 + i as u8);
    }
    // Numbers
    for (i, c) in ('1'..='9').enumerate() {
        let name: &'static str = Box::leak(format!("{c}").into_boxed_str());
        m.insert(name, 0x1E + i as u8);
    }
    m.insert("0", 0x27);
    // F-keys
    for i in 1..=12u8 {
        let name: &'static str = Box::leak(format!("F{i}").into_boxed_str());
        m.insert(name, 0x3A + i - 1);
    }
    // Modifiers
    m.insert("LeftCtrl", 0xE0);
    m.insert("LeftShift", 0xE1);
    m.insert("LeftAlt", 0xE2);
    m.insert("LeftGui", 0xE3);
    m.insert("RightCtrl", 0xE4);
    m.insert("RightShift", 0xE5);
    m.insert("RightAlt", 0xE6);
    m.insert("RightGui", 0xE7);
    // Common keys
    m.insert("Enter", 0x28);
    m.insert("Escape", 0x29);
    m.insert("Backspace", 0x2A);
    m.insert("Tab", 0x2B);
    m.insert("Space", 0x2C);
    m.insert("CapsLock", 0x39);
    m.insert("PrintScreen", 0x46);
    m.insert("ScrollLock", 0x47);
    m.insert("Pause", 0x48);
    m.insert("Insert", 0x49);
    m.insert("Home", 0x4A);
    m.insert("PageUp", 0x4B);
    m.insert("Delete", 0x4C);
    m.insert("End", 0x4D);
    m.insert("PageDown", 0x4E);
    m.insert("Right", 0x4F);
    m.insert("Left", 0x50);
    m.insert("Down", 0x51);
    m.insert("Up", 0x52);
    // Aliases
    m.insert("Ctrl", 0xE0);
    m.insert("Shift", 0xE1);
    m.insert("Alt", 0xE2);
    m
});

/// Resolve a key name to HID usage ID.
pub fn resolve_key(name: &str) -> Result<u8> {
    KEY_MAP
        .get(name)
        .copied()
        .ok_or_else(|| anyhow::anyhow!("unknown key name: {name}"))
}

/// Parse a remap target like "LeftCtrl" or "Ctrl+F12" into (modifier_mask, key_usage_id).
/// Returns (modifier_bits, usage_id) where modifier_bits is a bitmask:
///   bit 0 = LeftCtrl, bit 1 = LeftShift, bit 2 = LeftAlt, bit 3 = LeftGui
pub fn parse_remap_target(target: &str) -> Result<(u8, u8)> {
    let parts: Vec<&str> = target.split('+').collect();
    if parts.len() == 1 {
        return Ok((0, resolve_key(parts[0])?));
    }
    // Last part is the key, preceding parts are modifiers
    let key = resolve_key(parts.last().unwrap())?;
    let mut mods: u8 = 0;
    for &part in &parts[..parts.len() - 1] {
        let usage = resolve_key(part)?;
        match usage {
            0xE0 => mods |= 0x01, // LeftCtrl
            0xE1 => mods |= 0x02, // LeftShift
            0xE2 => mods |= 0x04, // LeftAlt
            0xE3 => mods |= 0x08, // LeftGui
            0xE4 => mods |= 0x10, // RightCtrl
            0xE5 => mods |= 0x20, // RightShift
            0xE6 => mods |= 0x40, // RightAlt
            0xE7 => mods |= 0x80, // RightGui
            _ => bail!("{part} is not a modifier key"),
        }
    }
    Ok((mods, key))
}
```

- [ ] **Step 2: Implement commands module**

```rust
// joro/src/commands.rs
// Last modified: <timestamp>

//! High-level Joro commands.
//!
//! Command class/id values come from openrazer PR #2683 and Phase 1 sniffing.
//! Remap and sleep class/id values MUST be updated with real values from
//! proto/docs/captured_commands.md.

use crate::packet::RazerPacket;
use crate::transport::RazerTransport;
use anyhow::{Context, Result};

pub fn set_brightness(transport: &dyn RazerTransport, brightness: u8) -> Result<()> {
    let pkt = RazerPacket::new(0x0F, 0x04, &[brightness]);
    let resp = transport.send(&pkt).context("set_brightness")?;
    check_status(&resp, "set_brightness")
}

pub fn get_brightness(transport: &dyn RazerTransport) -> Result<u8> {
    let pkt = RazerPacket::new(0x0F, 0x84, &[]);
    let resp = transport.send(&pkt).context("get_brightness")?;
    check_status(&resp, "get_brightness")?;
    Ok(*resp.args.first().unwrap_or(&0))
}

pub fn set_static_color(transport: &dyn RazerTransport, r: u8, g: u8, b: u8) -> Result<()> {
    let pkt = RazerPacket::new(0x0F, 0x02, &[r, g, b]);
    let resp = transport.send(&pkt).context("set_static_color")?;
    check_status(&resp, "set_static_color")
}

// --- Key Remapping ---
// REPLACE these with real values from proto/docs/captured_commands.md
const REMAP_CLASS: u8 = 0x00; // TODO: update from Phase 1 sniff
const REMAP_CMD_ID: u8 = 0x00; // TODO: update from Phase 1 sniff

pub fn set_key_remap(
    transport: &dyn RazerTransport,
    source_usage: u8,
    modifier_mask: u8,
    target_usage: u8,
) -> Result<()> {
    // Args layout from sniff — update to match actual packet format
    let pkt = RazerPacket::new(REMAP_CLASS, REMAP_CMD_ID, &[source_usage, modifier_mask, target_usage]);
    let resp = transport.send(&pkt).context("set_key_remap")?;
    check_status(&resp, "set_key_remap")
}

pub fn clear_remaps(transport: &dyn RazerTransport) -> Result<()> {
    let pkt = RazerPacket::new(REMAP_CLASS, REMAP_CMD_ID, &[]);
    let resp = transport.send(&pkt).context("clear_remaps")?;
    check_status(&resp, "clear_remaps")
}

// --- BLE Sleep Config ---
// REPLACE with real values from proto/docs/captured_commands.md
const SLEEP_CLASS: u8 = 0x00; // TODO: update from Phase 1 sniff
const SLEEP_CMD_ID: u8 = 0x00; // TODO: update from Phase 1 sniff

pub fn set_sleep_config(transport: &dyn RazerTransport, args: &[u8]) -> Result<()> {
    let pkt = RazerPacket::new(SLEEP_CLASS, SLEEP_CMD_ID, args);
    let resp = transport.send(&pkt).context("set_sleep_config")?;
    check_status(&resp, "set_sleep_config")
}

fn check_status(packet: &RazerPacket, cmd_name: &str) -> Result<()> {
    match packet.status {
        0x02 => Ok(()),
        0x03 => anyhow::bail!("{cmd_name}: device returned error (status 0x03)"),
        s => anyhow::bail!("{cmd_name}: unexpected status 0x{s:02X}"),
    }
}
```

- [ ] **Step 3: Wire CLI to real commands**

Replace `joro/src/main.rs`:

```rust
// joro/src/main.rs
// Last modified: <timestamp>

pub mod commands;
pub mod config;
pub mod keymap;
pub mod packet;
pub mod transport;

use anyhow::Result;
use clap::Parser;

use crate::config::JoroConfig;
use crate::keymap::{parse_remap_target, resolve_key};
use crate::transport::usb::UsbTransport;
use crate::transport::RazerTransport;

#[derive(Parser)]
#[command(name = "joro", about = "Razer Joro keyboard driver")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(clap::Subcommand)]
enum Commands {
    /// Set backlight color (hex RGB, e.g. FF6600)
    SetColor { color: String },
    /// Set backlight brightness (0-255)
    SetBrightness { value: u8 },
    /// Get current brightness
    GetBrightness,
    /// Set a key remap (e.g. joro remap CapsLock LeftCtrl)
    Remap { source: String, target: String },
    /// Clear all remaps
    ClearRemaps,
    /// Apply config from joro.toml
    Apply,
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let transport = UsbTransport::open()?;

    match cli.command {
        Commands::SetColor { color } => {
            let (r, g, b) = JoroConfig::parse_hex_color(&color)?;
            commands::set_static_color(&transport, r, g, b)?;
            println!("Color set to #{color}");
        }
        Commands::SetBrightness { value } => {
            commands::set_brightness(&transport, value)?;
            println!("Brightness set to {value}");
        }
        Commands::GetBrightness => {
            let b = commands::get_brightness(&transport)?;
            println!("Brightness: {b}");
        }
        Commands::Remap { source, target } => {
            let src = resolve_key(&source)?;
            let (mods, tgt) = parse_remap_target(&target)?;
            commands::set_key_remap(&transport, src, mods, tgt)?;
            println!("Remapped {source} -> {target}");
        }
        Commands::ClearRemaps => {
            commands::clear_remaps(&transport)?;
            println!("Remaps cleared");
        }
        Commands::Apply => {
            let config_path = dirs_config_path();
            let config = JoroConfig::load_or_create(&config_path)?;
            let (r, g, b) = JoroConfig::parse_hex_color(&config.lighting.color)?;
            commands::set_static_color(&transport, r, g, b)?;
            commands::set_brightness(&transport, config.lighting.brightness)?;
            for (source, target) in &config.remaps {
                let src = resolve_key(source)?;
                let (mods, tgt) = parse_remap_target(target)?;
                commands::set_key_remap(&transport, src, mods, tgt)?;
            }
            println!("Config applied from {}", config_path.display());
        }
    }
    Ok(())
}

fn dirs_config_path() -> std::path::PathBuf {
    let appdata = std::env::var("APPDATA").unwrap_or_else(|_| ".".into());
    std::path::PathBuf::from(appdata).join("joro").join("joro.toml")
}
```

Update `joro/src/lib.rs`:

```rust
// joro/src/lib.rs
pub mod commands;
pub mod config;
pub mod keymap;
pub mod packet;
pub mod transport;
```

- [ ] **Step 4: Add `dirs` or handle APPDATA manually**

No extra dependency needed — we use `APPDATA` env var directly (Windows standard).

- [ ] **Step 5: Build and test CLI**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
cargo run -- --help
cargo run -- get-brightness
cargo run -- set-color FF0000
cargo run -- set-brightness 200
```

Expected: commands execute against real hardware. Verify keyboard responds.

- [ ] **Step 6: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/commands.rs joro/src/keymap.rs joro/src/main.rs joro/src/lib.rs
git commit -m "feat: commands module and CLI integration - set color, brightness, remap"
```

---

## Phase 3: BLE + Dongle Transports

> **Prerequisite:** Phase 2 complete. USB transport working. BLE GATT characteristic identified from Phase 1.

---

### Task 15: BLE Transport (Rust)

**Files:**
- Create: `joro/src/transport/ble.rs`
- Modify: `joro/src/transport/mod.rs` — add `pub mod ble;`
- Modify: `joro/Cargo.toml` — uncomment btleplug + tokio

- [ ] **Step 1: Enable BLE dependencies in Cargo.toml**

Uncomment in `joro/Cargo.toml`:

```toml
btleplug = "0.11"
tokio = { version = "1", features = ["full"] }
```

- [ ] **Step 2: Implement BLE transport**

```rust
// joro/src/transport/ble.rs
// Last modified: <timestamp>

//! BLE transport via btleplug.
//!
//! Connects to Joro over BLE and sends/receives Razer HID packets
//! via the GATT characteristic identified in Phase 1.

use crate::packet::{RazerPacket, PACKET_SIZE};
use super::RazerTransport;
use anyhow::{Context, Result};
use btleplug::api::{Central, Manager as _, Peripheral as _, WriteType};
use btleplug::platform::{Adapter, Manager, Peripheral};
use std::sync::Mutex;
use std::time::Duration;
use tokio::runtime::Runtime;
use uuid::Uuid;

// REPLACE with actual GATT characteristic UUID from Phase 1 BLE exploration
const COMMAND_CHAR_UUID: &str = "00000000-0000-0000-0000-000000000000"; // TODO: update

pub struct BleTransport {
    peripheral: Mutex<Peripheral>,
    char_uuid: Uuid,
    runtime: Runtime,
}

impl BleTransport {
    pub fn connect() -> Result<Self> {
        let runtime = Runtime::new()?;
        let peripheral = runtime.block_on(async {
            let manager = Manager::new().await?;
            let adapters = manager.adapters().await?;
            let adapter = adapters.into_iter().next()
                .context("no BLE adapter found")?;

            adapter.start_scan(Default::default()).await?;
            tokio::time::sleep(Duration::from_secs(5)).await;

            let peripherals = adapter.peripherals().await?;
            for p in peripherals {
                if let Some(props) = p.properties().await? {
                    if let Some(name) = &props.local_name {
                        if name.contains("Razer") || name.contains("Joro") {
                            p.connect().await.context("BLE connect")?;
                            p.discover_services().await?;
                            return Ok::<_, anyhow::Error>(p);
                        }
                    }
                }
            }
            anyhow::bail!("Joro BLE device not found")
        })?;

        Ok(Self {
            peripheral: Mutex::new(peripheral),
            char_uuid: Uuid::parse_str(COMMAND_CHAR_UUID)?,
            runtime,
        })
    }
}

impl RazerTransport for BleTransport {
    fn send(&self, packet: &RazerPacket) -> Result<RazerPacket> {
        let peripheral = self.peripheral.lock().unwrap();
        let buf = packet.to_bytes();

        self.runtime.block_on(async {
            let chars = peripheral.characteristics();
            let cmd_char = chars.iter()
                .find(|c| c.uuid == self.char_uuid)
                .context("command characteristic not found")?;

            peripheral.write(cmd_char, &buf, WriteType::WithResponse).await
                .context("BLE write")?;

            tokio::time::sleep(Duration::from_millis(50)).await;

            let resp = peripheral.read(cmd_char).await
                .context("BLE read response")?;

            if resp.len() < PACKET_SIZE {
                anyhow::bail!("BLE response too short: {} bytes", resp.len());
            }
            RazerPacket::from_bytes(&resp)
        })
    }

    fn is_connected(&self) -> bool {
        let peripheral = self.peripheral.lock().unwrap();
        self.runtime.block_on(async {
            peripheral.is_connected().await.unwrap_or(false)
        })
    }
}
```

Add to `joro/src/transport/mod.rs`:

```rust
pub mod ble;
```

- [ ] **Step 3: Build and test with Joro in BLE mode**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
cargo run -- get-brightness  # will fail — still uses USB transport in main.rs
```

For now, verify it compiles. Transport selection (auto-detect) comes in Task 16.

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/transport/ble.rs joro/src/transport/mod.rs joro/Cargo.toml
git commit -m "feat: BLE transport via btleplug"
```

---

### Task 16: Dongle Transport + Auto-Detection

**Files:**
- Create: `joro/src/transport/dongle.rs`
- Create: `joro/src/device.rs`
- Modify: `joro/src/transport/mod.rs` — add `pub mod dongle;`
- Modify: `joro/src/main.rs` — use DeviceManager instead of direct UsbTransport
- Modify: `joro/src/lib.rs` — add `pub mod device;`

- [ ] **Step 1: Implement dongle transport**

```rust
// joro/src/transport/dongle.rs
// Last modified: <timestamp>

//! 2.4GHz dongle transport via hidapi.
//!
//! Same as UsbTransport but targets the dongle PID.

use crate::packet::{RazerPacket, PACKET_SIZE};
use super::RazerTransport;
use anyhow::{Context, Result};
use std::sync::Mutex;

const RAZER_VID: u16 = 0x1532;
// REPLACE with actual dongle PID from Phase 1 enumeration
const DONGLE_PID: u16 = 0x0000; // TODO: update

pub struct DongleTransport {
    device: Mutex<hidapi::HidDevice>,
}

impl DongleTransport {
    pub fn open() -> Result<Self> {
        let api = hidapi::HidApi::new().context("initializing hidapi")?;
        let device = api.open(RAZER_VID, DONGLE_PID)
            .context("opening Razer 2.4GHz dongle")?;
        Ok(Self { device: Mutex::new(device) })
    }

    pub fn is_available() -> bool {
        hidapi::HidApi::new()
            .map(|api| {
                api.device_list()
                    .any(|d| d.vendor_id() == RAZER_VID && d.product_id() == DONGLE_PID)
            })
            .unwrap_or(false)
    }
}

impl RazerTransport for DongleTransport {
    fn send(&self, packet: &RazerPacket) -> Result<RazerPacket> {
        let dev = self.device.lock().unwrap();
        let buf = packet.to_bytes();

        let mut report = vec![0x00u8];
        report.extend_from_slice(&buf[1..]);
        dev.send_feature_report(&report).context("send_feature_report")?;

        std::thread::sleep(std::time::Duration::from_millis(20));

        let mut resp_buf = vec![0u8; PACKET_SIZE + 1];
        let n = dev.get_feature_report(&mut resp_buf).context("get_feature_report")?;

        let resp_start = if resp_buf[0] == 0x00 && n > PACKET_SIZE { 1 } else { 0 };
        RazerPacket::from_bytes(&resp_buf[resp_start..resp_start + PACKET_SIZE])
    }

    fn is_connected(&self) -> bool {
        let dev = self.device.lock().unwrap();
        let mut buf = vec![0u8; PACKET_SIZE + 1];
        dev.get_feature_report(&mut buf).is_ok()
    }
}
```

- [ ] **Step 2: Implement device manager**

```rust
// joro/src/device.rs
// Last modified: <timestamp>

//! Device manager — auto-detect transport, monitor, reconnect.

use crate::transport::{RazerTransport, usb::UsbTransport, dongle::DongleTransport, ble::BleTransport};
use anyhow::Result;

pub enum ActiveTransport {
    Usb,
    Dongle,
    Ble,
}

pub struct DeviceManager {
    transport: Box<dyn RazerTransport>,
    active: ActiveTransport,
}

impl DeviceManager {
    /// Probe transports in priority order: USB > Dongle > BLE.
    pub fn connect() -> Result<Self> {
        // Try USB first
        if let Ok(t) = UsbTransport::open() {
            println!("Connected via USB");
            return Ok(Self { transport: Box::new(t), active: ActiveTransport::Usb });
        }

        // Try 2.4GHz dongle
        if DongleTransport::is_available() {
            if let Ok(t) = DongleTransport::open() {
                println!("Connected via 2.4GHz dongle");
                return Ok(Self { transport: Box::new(t), active: ActiveTransport::Dongle });
            }
        }

        // Try BLE
        if let Ok(t) = BleTransport::connect() {
            println!("Connected via BLE");
            return Ok(Self { transport: Box::new(t), active: ActiveTransport::Ble });
        }

        anyhow::bail!("No Joro device found on any transport (USB, dongle, BLE)")
    }

    pub fn transport(&self) -> &dyn RazerTransport {
        self.transport.as_ref()
    }

    pub fn transport_name(&self) -> &str {
        match self.active {
            ActiveTransport::Usb => "USB",
            ActiveTransport::Dongle => "2.4GHz",
            ActiveTransport::Ble => "BLE",
        }
    }

    pub fn is_connected(&self) -> bool {
        self.transport.is_connected()
    }
}
```

- [ ] **Step 3: Update main.rs to use DeviceManager**

In `joro/src/main.rs`, replace:

```rust
let transport = UsbTransport::open()?;
```

with:

```rust
let device = crate::device::DeviceManager::connect()?;
let transport = device.transport();
```

And update all `&transport` references to `transport` (it's already `&dyn RazerTransport`).

Add to `joro/src/lib.rs`:

```rust
pub mod device;
```

- [ ] **Step 4: Build and test auto-detection**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
# With USB connected:
cargo run -- get-brightness
# Should print "Connected via USB" then the brightness
```

- [ ] **Step 5: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/transport/dongle.rs joro/src/device.rs joro/src/main.rs joro/src/lib.rs joro/src/transport/mod.rs
git commit -m "feat: dongle transport and device manager with auto-detection"
```

---

### Task 17: BLE Sleep Fix Integration

**Files:**
- Modify: `joro/src/device.rs` — apply sleep config on BLE connect
- Modify: `joro/src/commands.rs` — ensure sleep config uses real values

- [ ] **Step 1: Update sleep config command with real values**

In `joro/src/commands.rs`, replace the placeholder sleep class/id with the real values from `proto/docs/captured_commands.md`. Also update the args to match the captured packet.

- [ ] **Step 2: Apply sleep config on BLE connect**

Add to `DeviceManager::connect()` after BLE connection succeeds:

```rust
// After BLE connect, apply sleep config
if let ActiveTransport::Ble = self.active {
    // REPLACE args with actual sleep config bytes from Phase 1
    let sleep_args: &[u8] = &[/* from captured_commands.md */];
    if let Err(e) = crate::commands::set_sleep_config(self.transport(), sleep_args) {
        eprintln!("Warning: failed to set sleep config: {e}");
    }
}
```

- [ ] **Step 3: Test BLE sleep behavior**

1. Connect Joro via BLE
2. Run `cargo run -- apply`
3. Wait 30+ seconds (trigger idle/sleep)
4. Press a key — verify reconnect is fast (should be near-instant vs. multi-second before)

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/device.rs joro/src/commands.rs
git commit -m "feat: BLE sleep config applied on connect - fixes reconnect delay"
```

---

## Phase 4: Systray + WebView UI

> **Prerequisite:** Phase 3 complete. All transports working.

---

### Task 18: Systray + WebView Scaffolding

**Files:**
- Create: `joro/src/ui/mod.rs`
- Create: `joro/src/ui/assets/index.html`
- Create: `joro/src/ui/assets/style.css`
- Create: `joro/src/ui/assets/app.js`
- Create: `joro/build.rs`
- Modify: `joro/Cargo.toml` — uncomment tao + wry, add include_dir
- Modify: `joro/src/main.rs` — add UI startup path

- [ ] **Step 1: Enable UI dependencies**

Update `joro/Cargo.toml`:

```toml
tao = "0.30"
wry = "0.47"
serde_json = "1"  # already present
```

- [ ] **Step 2: Create HTML settings panel**

```html
<!-- joro/src/ui/assets/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Joro Settings</title>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <header>
    <h1>Joro Settings</h1>
  </header>

  <section id="lighting">
    <h2>Lighting</h2>
    <div class="field">
      <label for="color-picker">Color</label>
      <input type="color" id="color-picker" value="#FF6600">
    </div>
    <div class="field">
      <label for="brightness">Brightness</label>
      <input type="range" id="brightness" min="0" max="255" value="200">
      <span id="brightness-value">200</span>
    </div>
  </section>

  <section id="remaps">
    <h2>Key Remaps</h2>
    <div id="remap-list"></div>
    <button id="add-remap">+ Add Remap</button>
  </section>

  <section id="connection">
    <h2>Connection</h2>
    <div class="field">
      <span>Status: </span><span id="conn-status">Unknown</span>
    </div>
    <div class="field">
      <span>Transport: </span><span id="conn-transport">—</span>
    </div>
  </section>

  <footer>
    <button id="apply-btn">Apply</button>
    <button id="save-btn">Save</button>
  </footer>

  <script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 3: Create CSS**

```css
/* joro/src/ui/assets/style.css */
* { margin: 0; padding: 0; box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  background: #1a1a2e;
  color: #e0e0e0;
  padding: 16px;
  width: 400px;
}
header h1 {
  font-size: 18px;
  margin-bottom: 16px;
  color: #00d4aa;
}
section {
  background: #16213e;
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
}
h2 {
  font-size: 14px;
  margin-bottom: 8px;
  color: #8888aa;
  text-transform: uppercase;
  letter-spacing: 1px;
}
.field {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.field label { min-width: 80px; }
input[type="range"] { flex: 1; }
input[type="color"] {
  width: 40px; height: 30px;
  border: none; cursor: pointer;
  background: none;
}
.remap-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.remap-row input {
  flex: 1;
  background: #0f3460;
  border: 1px solid #333;
  color: #e0e0e0;
  padding: 4px 8px;
  border-radius: 4px;
}
.remap-row .arrow { color: #00d4aa; }
.remap-row button {
  background: #e94560;
  color: white;
  border: none;
  border-radius: 4px;
  padding: 4px 8px;
  cursor: pointer;
}
#add-remap {
  background: none;
  border: 1px dashed #444;
  color: #8888aa;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  width: 100%;
  margin-top: 4px;
}
footer {
  display: flex;
  gap: 8px;
  justify-content: flex-end;
}
footer button {
  padding: 8px 20px;
  border: none;
  border-radius: 6px;
  cursor: pointer;
  font-weight: 600;
}
#apply-btn {
  background: #0f3460;
  color: #e0e0e0;
}
#save-btn {
  background: #00d4aa;
  color: #1a1a2e;
}
#conn-status { font-weight: 600; }
```

- [ ] **Step 4: Create JS**

```javascript
// joro/src/ui/assets/app.js

// IPC: call Rust backend
function invoke(cmd, args) {
  window.ipc.postMessage(JSON.stringify({ cmd, args }));
}

// Brightness slider
const brightnessSlider = document.getElementById('brightness');
const brightnessValue = document.getElementById('brightness-value');
brightnessSlider.addEventListener('input', () => {
  brightnessValue.textContent = brightnessSlider.value;
});

// Color picker — live preview
const colorPicker = document.getElementById('color-picker');
colorPicker.addEventListener('input', () => {
  invoke('preview_color', { color: colorPicker.value, brightness: parseInt(brightnessSlider.value) });
});

brightnessSlider.addEventListener('change', () => {
  invoke('preview_color', { color: colorPicker.value, brightness: parseInt(brightnessSlider.value) });
});

// Remap management
const remapList = document.getElementById('remap-list');
const addRemapBtn = document.getElementById('add-remap');

function addRemapRow(source = '', target = '') {
  const row = document.createElement('div');
  row.className = 'remap-row';
  row.innerHTML = `
    <input type="text" placeholder="Key" value="${source}">
    <span class="arrow">&rarr;</span>
    <input type="text" placeholder="Target" value="${target}">
    <button class="remove-remap">&times;</button>
  `;
  row.querySelector('.remove-remap').addEventListener('click', () => row.remove());
  remapList.appendChild(row);
}

addRemapBtn.addEventListener('click', () => addRemapRow());

// Apply button
document.getElementById('apply-btn').addEventListener('click', () => {
  const remaps = {};
  remapList.querySelectorAll('.remap-row').forEach(row => {
    const inputs = row.querySelectorAll('input');
    if (inputs[0].value && inputs[1].value) {
      remaps[inputs[0].value] = inputs[1].value;
    }
  });
  invoke('apply', {
    color: colorPicker.value,
    brightness: parseInt(brightnessSlider.value),
    remaps
  });
});

// Save button
document.getElementById('save-btn').addEventListener('click', () => {
  const remaps = {};
  remapList.querySelectorAll('.remap-row').forEach(row => {
    const inputs = row.querySelectorAll('input');
    if (inputs[0].value && inputs[1].value) {
      remaps[inputs[0].value] = inputs[1].value;
    }
  });
  invoke('save', {
    color: colorPicker.value,
    brightness: parseInt(brightnessSlider.value),
    remaps
  });
});

// Receive state updates from Rust
window.updateState = function(state) {
  if (state.color) colorPicker.value = state.color;
  if (state.brightness !== undefined) {
    brightnessSlider.value = state.brightness;
    brightnessValue.textContent = state.brightness;
  }
  if (state.remaps) {
    remapList.innerHTML = '';
    for (const [src, tgt] of Object.entries(state.remaps)) {
      addRemapRow(src, tgt);
    }
  }
  if (state.connected !== undefined) {
    document.getElementById('conn-status').textContent = state.connected ? 'Connected' : 'Disconnected';
    document.getElementById('conn-status').style.color = state.connected ? '#00d4aa' : '#e94560';
  }
  if (state.transport) {
    document.getElementById('conn-transport').textContent = state.transport;
  }
};
```

- [ ] **Step 5: Implement Rust UI module (systray + webview)**

```rust
// joro/src/ui/mod.rs
// Last modified: <timestamp>

//! Systray icon + webview settings panel.

use crate::commands;
use crate::config::JoroConfig;
use crate::device::DeviceManager;
use crate::keymap::{parse_remap_target, resolve_key};
use anyhow::Result;
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use tao::event::{Event, WindowEvent};
use tao::event_loop::{ControlFlow, EventLoop};
use tao::window::WindowBuilder;
use wry::WebViewBuilder;

const HTML: &str = include_str!("assets/index.html");
const CSS: &str = include_str!("assets/style.css");
const JS: &str = include_str!("assets/app.js");

pub fn run(device: Arc<Mutex<DeviceManager>>, config_path: std::path::PathBuf) -> Result<()> {
    let config = Arc::new(Mutex::new(
        JoroConfig::load_or_create(&config_path)?
    ));

    let event_loop = EventLoop::new();
    let window = WindowBuilder::new()
        .with_title("Joro Settings")
        .with_inner_size(tao::dpi::LogicalSize::new(400.0, 500.0))
        .with_resizable(false)
        .build(&event_loop)?;

    // Inline CSS and JS into HTML
    let full_html = HTML
        .replace(
            r#"<link rel="stylesheet" href="style.css">"#,
            &format!("<style>{CSS}</style>"),
        )
        .replace(
            r#"<script src="app.js"></script>"#,
            &format!("<script>{JS}</script>"),
        );

    let device_clone = device.clone();
    let config_clone = config.clone();
    let config_path_clone = config_path.clone();

    let webview = WebViewBuilder::new()
        .with_html(&full_html)
        .with_ipc_handler(move |msg| {
            let msg_str = msg.body();
            if let Ok(parsed) = serde_json::from_str::<serde_json::Value>(msg_str) {
                let cmd = parsed["cmd"].as_str().unwrap_or("");
                let args = &parsed["args"];
                handle_ipc(cmd, args, &device_clone, &config_clone, &config_path_clone);
            }
        })
        .build(&window)?;

    // Send initial state to webview
    {
        let cfg = config.lock().unwrap();
        let dev = device.lock().unwrap();
        let state = serde_json::json!({
            "color": cfg.lighting.color,
            "brightness": cfg.lighting.brightness,
            "remaps": cfg.remaps,
            "connected": dev.is_connected(),
            "transport": dev.transport_name(),
        });
        let _ = webview.evaluate_script(&format!("window.updateState({})", state));
    }

    event_loop.run(move |event, _, control_flow| {
        *control_flow = ControlFlow::Wait;
        if let Event::WindowEvent { event: WindowEvent::CloseRequested, .. } = event {
            // Hide window instead of quitting (systray keeps running)
            // For now, just exit — systray integration refines this
            *control_flow = ControlFlow::Exit;
        }
    });
}

fn handle_ipc(
    cmd: &str,
    args: &serde_json::Value,
    device: &Arc<Mutex<DeviceManager>>,
    config: &Arc<Mutex<JoroConfig>>,
    config_path: &std::path::Path,
) {
    match cmd {
        "preview_color" => {
            if let (Some(color), Some(brightness)) = (args["color"].as_str(), args["brightness"].as_u64()) {
                if let Ok((r, g, b)) = JoroConfig::parse_hex_color(color) {
                    let dev = device.lock().unwrap();
                    let _ = commands::set_static_color(dev.transport(), r, g, b);
                    let _ = commands::set_brightness(dev.transport(), brightness as u8);
                }
            }
        }
        "apply" => {
            apply_from_args(args, device);
        }
        "save" => {
            apply_from_args(args, device);
            let mut cfg = config.lock().unwrap();
            if let Some(color) = args["color"].as_str() {
                cfg.lighting.color = color.into();
            }
            if let Some(b) = args["brightness"].as_u64() {
                cfg.lighting.brightness = b as u8;
            }
            if let Some(remaps) = args["remaps"].as_object() {
                cfg.remaps = remaps.iter()
                    .filter_map(|(k, v)| v.as_str().map(|s| (k.clone(), s.to_string())))
                    .collect();
            }
            if let Err(e) = cfg.save(config_path) {
                eprintln!("Failed to save config: {e}");
            }
        }
        _ => {}
    }
}

fn apply_from_args(args: &serde_json::Value, device: &Arc<Mutex<DeviceManager>>) {
    let dev = device.lock().unwrap();
    if let (Some(color), Some(brightness)) = (args["color"].as_str(), args["brightness"].as_u64()) {
        if let Ok((r, g, b)) = JoroConfig::parse_hex_color(color) {
            let _ = commands::set_static_color(dev.transport(), r, g, b);
            let _ = commands::set_brightness(dev.transport(), brightness as u8);
        }
    }
    if let Some(remaps) = args["remaps"].as_object() {
        let _ = commands::clear_remaps(dev.transport());
        for (source, target) in remaps {
            if let Some(target_str) = target.as_str() {
                if let (Ok(src), Ok((mods, tgt))) = (resolve_key(source), parse_remap_target(target_str)) {
                    let _ = commands::set_key_remap(dev.transport(), src, mods, tgt);
                }
            }
        }
    }
}
```

Add `pub mod ui;` to `joro/src/lib.rs`.

- [ ] **Step 6: Update main.rs with UI startup**

Add a new `Run` subcommand (default mode) to `main.rs`:

```rust
/// Run with systray UI (default mode)
Run,
```

And in the match:

```rust
Commands::Run => {
    let device = Arc::new(Mutex::new(crate::device::DeviceManager::connect()?));
    let config_path = dirs_config_path();

    // Apply config on startup
    {
        let dev = device.lock().unwrap();
        let config = JoroConfig::load_or_create(&config_path)?;
        let (r, g, b) = JoroConfig::parse_hex_color(&config.lighting.color)?;
        let _ = commands::set_static_color(dev.transport(), r, g, b);
        let _ = commands::set_brightness(dev.transport(), config.lighting.brightness);
    }

    crate::ui::run(device, config_path)?;
}
```

- [ ] **Step 7: Build and verify UI opens**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build
cargo run -- run
```

Expected: settings window opens with color picker, brightness slider, remap editor, connection status. Changing color should update the keyboard live.

- [ ] **Step 8: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/ui/ joro/src/main.rs joro/src/lib.rs joro/Cargo.toml
git commit -m "feat: systray + webview settings UI with live preview"
```

---

## Phase 5: Polish & Packaging

---

### Task 19: Autostart & First-Run

**Files:**
- Modify: `joro/src/main.rs` — add `--minimized` flag, `install`/`uninstall` subcommands

- [ ] **Step 1: Add autostart registry commands**

Add to `main.rs` CLI:

```rust
/// Install autostart (adds to Windows startup)
Install,
/// Remove autostart
Uninstall,
```

Implement:

```rust
Commands::Install => {
    let exe = std::env::current_exe()?.display().to_string();
    let output = std::process::Command::new("reg")
        .args(["add", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
               "/v", "Joro", "/t", "REG_SZ", "/d", &format!("{exe} run --minimized"), "/f"])
        .output()?;
    if output.status.success() {
        println!("Autostart installed");
    } else {
        eprintln!("Failed: {}", String::from_utf8_lossy(&output.stderr));
    }
}
Commands::Uninstall => {
    let output = std::process::Command::new("reg")
        .args(["delete", r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
               "/v", "Joro", "/f"])
        .output()?;
    if output.status.success() {
        println!("Autostart removed");
    } else {
        eprintln!("Failed: {}", String::from_utf8_lossy(&output.stderr));
    }
}
```

- [ ] **Step 2: Add --minimized flag to Run subcommand**

```rust
/// Run with systray UI (default mode)
Run {
    #[arg(long)]
    minimized: bool,
},
```

When `minimized` is true, skip opening the webview window on startup — just show the systray icon and apply config.

- [ ] **Step 3: Test install/uninstall**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo run -- install
# Check: regedit -> HKCU\Software\Microsoft\Windows\CurrentVersion\Run -> "Joro" entry
cargo run -- uninstall
# Check: entry removed
```

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/src/main.rs
git commit -m "feat: autostart install/uninstall and --minimized flag"
```

---

### Task 20: Release Build & Final Verification

**Files:**
- Modify: `joro/Cargo.toml` — release profile settings

- [ ] **Step 1: Configure release profile**

Add to `joro/Cargo.toml`:

```toml
[profile.release]
opt-level = "z"    # optimize for size
lto = true
strip = true
```

- [ ] **Step 2: Build release binary**

```bash
cd L:/PROJECTS/razer-joro/joro
cargo build --release
ls -la target/release/joro.exe
```

Expected: single `.exe`, ideally under 10MB.

- [ ] **Step 3: End-to-end verification**

1. Kill Synapse completely
2. Run `joro.exe run` from release build
3. Verify: color picker works, brightness slider works, remap works
4. Close window — verify it exits cleanly
5. Run `joro.exe run --minimized` — verify it starts silently
6. Disconnect USB, connect BLE — verify auto-reconnect and sleep fix
7. Run `joro.exe install` — verify autostart registry entry

- [ ] **Step 4: Commit**

```bash
cd L:/PROJECTS/razer-joro
git add joro/Cargo.toml
git commit -m "feat: release build profile - optimized for size"
```

---

## Summary

| Phase | Tasks | Focus |
|-------|-------|-------|
| 1: Python Prototype | 1-9 | RE, sniffing, protocol validation |
| 2: Rust Core | 10-14 | Packet, config, USB transport, CLI |
| 3: BLE + Dongle | 15-17 | Wireless transports, sleep fix |
| 4: UI | 18 | Systray + webview settings panel |
| 5: Polish | 19-20 | Autostart, release build |

**Total: 20 tasks.** Phase 1 is exploratory (hardware-dependent). Phases 2-5 are implementation with TDD where applicable.

**Hard dependencies on Phase 1 output:**
- Remap command class/id/args → Tasks 6, 14
- Sleep config class/id/args → Tasks 8, 17
- 2.4GHz dongle PID → Task 16
- BLE GATT characteristic UUID → Task 15
