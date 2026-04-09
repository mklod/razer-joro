# Razer Joro — Changelog

## TODO
> [!tip] Queued for next build
> - Map full keyboard matrix indices (find CapsLock, modifiers, etc.)
> - Test modifier combo remaps (e.g., CapsLock -> Ctrl+F12)
> - Decode and test sleep/idle SET commands (class 0x06)
> - BLE GATT exploration with bleak
> - Write formal `proto/test_remap.py` with proper index table
> - Begin Rust phase (scaffold, packet builder, config module)

## Build 2026-04-09--1630
**Changes**
- Rewrote `proto/razer_packet.py` — corrected to openrazer `razer_report` struct layout (status@0, args@8)
- Rewrote `proto/usb_transport.py` — switched from hidapi to pyusb raw USB control transfers
- Created `proto/commands.py` — set_static_color, set_brightness, get_brightness, get_firmware, set_effect_none
- Created `proto/test_lighting.py` — interactive lighting test script
- Created `proto/scan_keymap.py`, `proto/scan_keymap_full.py`, `proto/find_capslock.py` — keymap RE tools
- Cloned openrazer PR branch to `openrazer-ref/`
- Installed Wireshark + USBPcap (USBPcap broken on this system, not needed)
- Added pyusb + libusb to requirements.txt
- Full brute-force command scan — discovered all supported class/id pairs

**Protocol Discoveries**
- Transport: pyusb control transfers (hidapi returns 0x05 not supported)
- LED: VARSTORE=0x01, BACKLIGHT_LED=0x05 (not 0x00)
- Keymap: GET 0x02/0x8F, SET 0x02/0x0F — matrix index-based entries
- Sleep: GET 0x06/0x86 (idle timeout), GET 0x06/0x8E (extended power config)
- Supported command classes: 0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0A, 0x0F

**Hardware Verification**
- Static color RED/GREEN/BLUE — confirmed working
- Brightness 0-255 — confirmed working
- Key remap backtick->A — confirmed working
- Key remap batch (indices 20-35) — confirmed affects real key output

> [!warning] Testing Checklist
> - [x] Static color set via `test_lighting.py` — observed on keyboard
>   - Notes: Verified RED, GREEN, BLUE, ORANGE color cycle
> - [x] Brightness control — observed on keyboard
>   - Notes: 0-255 range works, get_brightness reads back correctly
> - [x] Key remap (single key) — verified backtick->A on hardware
>   - Notes: Index 1 = backtick key. SET 0x02/0x0F with send-only (no GET response after SET)
> - [ ] CapsLock remap — matrix index not yet identified
>   - Notes: Confirmed in range 20-35, currently mapped to F-keys for identification
> - [ ] Modifier combo remap (e.g., Ctrl+F12)
>   - Notes: Not yet tested
> - [ ] Sleep/idle config SET command
>   - Notes: GET works, SET not yet tested
> - [ ] BLE connection and command test
>   - Notes: Not yet attempted

## Build 2026-04-09--1430
**Changes**
- Created project directory and research spec (`razer-joro-synapse-replacement.md`)
- Added firmware updater exe
- Set up project docs (`_status.md`, `WORKPLAN.md`, `CHANGELOG.md`)
- Created design spec and implementation plan

> [!warning] Testing Checklist
> - [x] Verify Joro is detected via `hid.enumerate(0x1532, 0x02CD)` on Windows
>   - Notes: 10 HID interfaces found. Interface 3 (MI_03) used for control.
> - [x] Confirm openrazer PR branch is accessible and cloneable
>   - Notes: Cloned to `openrazer-ref/`
