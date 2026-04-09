# Razer Joro — Synapse Replacement Workplan

## Project Summary
Lightweight Windows background process to replace Razer Synapse for the Razer Joro keyboard (VID/PID `1532:02CD`). Covers backlight control, key remapping, and BLE sleep fix. Python prototype phase for RE, then Rust production service with systray + webview UI.

## Tech Stack
- **Prototype:** Python 3, pyusb, libusb, bleak
- **Production:** Rust, rusb (not hidapi-rs), btleplug, tao+wry
- **USB capture:** Not needed — brute-force command scan via pyusb
- **Reference:** openrazer PR #2683 (madbrainz/openrazer, branch `add-razer-joro-support`)

## Key Files
- `razer-joro-synapse-replacement.md` — original research spec
- `docs/superpowers/specs/2026-04-09-joro-synapse-replacement-design.md` — design spec
- `docs/superpowers/plans/2026-04-09-joro-synapse-replacement.md` — implementation plan
- `proto/` — Python prototype scripts
- `openrazer-ref/` — cloned openrazer PR branch for reference

## Critical Protocol Notes
- **Transport:** Must use raw USB control transfers (pyusb/rusb), NOT HID feature reports (hidapi)
- **Packet:** openrazer `razer_report` struct (90 bytes), transaction_id=0x1F, wIndex=0x03
- **LED:** VARSTORE=0x01, BACKLIGHT_LED=0x05
- **Remaps:** Matrix index-based, not HID usage-based. USB replug resets all remaps.

---

## Stages

### Stage 1: Research & Reference — `COMPLETE`
- [x] Clone openrazer PR branch, study Huntsman V3 Pro base class
- [x] Document all known command_class / command_id pairs for Joro
- [x] ~~Set up Wireshark + USBPcap~~ Replaced with brute-force command scan

### Stage 2: Python Prototype & Command Validation — `IN PROGRESS`
- [x] Basic packet builder with CRC (openrazer struct layout)
- [x] USB transport via pyusb control transfers
- [x] HID device enumeration
- [x] Lighting commands verified on hardware (static color + brightness)
- [x] Brute-force command scan — all supported commands discovered
- [x] Key remap SET/GET confirmed working (backtick->A verified)
- [ ] Map full keyboard matrix indices (CapsLock index search in progress)
- [ ] Test modifier combo remaps
- [ ] Decode and test sleep/idle config (class 0x06)
- [ ] BLE GATT exploration with bleak
- [ ] ~~Capture Synapse key remap traffic~~ Not needed — direct command testing works
- [ ] ~~Capture Synapse idle/power config~~ Not needed — commands discovered via scan

### Stage 3: Rust Core (Transport + Config) — `TODO`
- [ ] Implement `RazerPacket` builder with CRC (Rust)
- [ ] Implement USB transport via `rusb` (NOT hidapi-rs — control transfers required)
- [ ] Config loader (TOML)
- [ ] CLI test harness
- [ ] **Note:** Plan needs update — must use `rusb` crate instead of `hidapi-rs`

### Stage 4: BLE + Dongle Transports — `TODO`
- [ ] BLE transport via btleplug
- [ ] Dongle transport (PID TBD from enumeration)
- [ ] Transport auto-detection and switching
- [ ] BLE sleep fix applied on connect

### Stage 5: Systray + WebView UI — `TODO`
- [ ] Systray via tao, webview via wry
- [ ] HTML/CSS/JS settings panel
- [ ] Live color/brightness preview
- [ ] Remap editor, connection status

### Stage 6: Polish & Packaging — `TODO`
- [ ] Autostart registration
- [ ] First-run config creation
- [ ] Error handling
- [ ] Single `.exe` release build
