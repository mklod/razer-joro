# Razer Joro — Synapse Replacement Project

## Goals

### Primary
- Replace Razer Synapse entirely with a lightweight background process
- **Backlight control** — static color and basic effects
- **Key remapping**
- **Fix BLE sleep/reconnect delay** (multi-second unresponsive period after inactivity in BLE mode)

### Stretch
- **2.4GHz dongle support** — either via Razer's own dongle or a custom/generic one

---

## Hardware

- **Device:** Razer Joro keyboard
- **USB VID/PID:** `1532:02CD`
- **Connection modes:** USB wired, BLE, Razer 2.4GHz dongle
- **Protocol:** Same as Huntsman V3 Pro — `report_index 0x03`, `transaction_id 0x1F`
- **Onboard memory:** ❌ None in use — keyboard reverts to stock when Synapse is killed. Requires a host process running at all times.

---

## Key Findings

### Protocol
- Razer uses a standard **90-byte USB HID control transfer** packet structure across all keyboards:
  ```
  [0x00] report_id
  [0x01] status
  [0x02] transaction_id  (0x1F for Joro)
  [0x03-04] data_size
  [0x05] command_class
  [0x06] command_id
  [0x07-87] arguments
  [0x88] CRC (XOR of bytes 2–87)
  [0x89] reserved
  ```
- Joro-specific: `report_index = 0x03`, `transaction_id = 0x1F`

### openrazer PR
- **PR #2683** on `openrazer/openrazer` adds full Joro support (open, not yet merged as of April 2026)
- Author: `madbrainz`, branch: `madbrainz/openrazer:add-razer-joro-support`
- **Confirmed working on Linux:** static color, wave, spectrum, breath, starlight, reactive, brightness, custom matrix
- Minor issues blocking merge: formatting lint failure, reviewer suggests different base class (`_RippleKeyboard`)
- This PR is the **reference implementation** for lighting — no RE needed for that part

### BLE Sleep Issue
- Cause: standard BLE **supervision timeout** / connection interval behavior, not a firmware bug
- Wired USB connection is always responsive — issue is BLE-mode only
- Synapse likely writes a BLE power/sleep config to the keyboard at runtime
- Fix: sniff the Synapse idle/power config packet over USB, replay it in the replacement driver

### Synapse Architecture
- Synapse 3 is an **Electron app** — can be unpacked with `asar extract`
- Native HID layer is in `.dll` plugins — reversible with Ghidra/x64dbg
- All useful protocol logic can be captured via USB sniffing without touching the app

---

## What Still Needs Reversing

| Feature | Status | Method |
|---|---|---|
| Lighting | ✅ Done (openrazer PR) | Use PR as reference |
| Key remapping | ❌ Unknown | USB sniff from Synapse |
| BLE sleep config | ❌ Unknown | USB sniff idle/power settings in Synapse |
| 2.4GHz dongle PID | ❌ Unknown | Enumerate with `hid.enumerate(0x1532, 0)` |
| 2.4GHz packet format | 🔶 Likely identical to USB wired | Verify by testing same packets against dongle |

---

## Recommended Toolchain

| Tool | Purpose |
|---|---|
| Wireshark + USBPcap | USB HID traffic capture (wired) |
| `hidapi` / `hid` Python lib | Send raw HID packets on Windows |
| `bleak` (Python) | BLE GATT enumeration and comms |
| nRF Sniffer + Wireshark | BLE air-side packet capture (optional) |
| Android HCI snoop log | Cheap BLE capture alternative |
| `asar` (npm) | Unpack Synapse Electron bundle |
| Ghidra | Reverse native Synapse DLLs if needed |
| openrazer source | Reference for all known Razer packet formats |

---

## Immediate Next Steps

1. **Clone the PR branch** — `madbrainz/openrazer` @ `add-razer-joro-support` — study `keyboards.py` and the Huntsman V3 Pro base class
2. **USB sniff session** — connect wired, capture Synapse doing: key remap, idle/power config change, and optionally the pending firmware update
3. **Windows HID prototype** — port openrazer lighting commands to Python + `hidapi` targeting `1532:02CD`
4. **Test 2.4GHz dongle** — enumerate its PID, try sending same HID packets
5. **BLE keepalive** — once sleep config packet is identified from sniff, add it to the host driver startup sequence

---

## Reference Links

- openrazer PR #2683: https://github.com/openrazer/openrazer/pull/2683
- PR branch: https://github.com/madbrainz/openrazer/tree/add-razer-joro-support
- openrazer issue #2540 (Joro support request): https://github.com/openrazer/openrazer/issues/2540
- openrazer main repo: https://github.com/openrazer/openrazer
