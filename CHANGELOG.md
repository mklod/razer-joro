# Razer Joro — Changelog

## TODO
> [!tip] Queued for next build
> - Test 2.4GHz dongle (PID 0x02CE)
> - Map remaining keyboard indices (only 1-8 + 30 known)
> - Windows autostart registration
> - Release build + single exe packaging
> - Config polish (more key names, validation)
> - **INVESTIGATE: Keyboard has persistent remap storage — likely F-keys only.** F4 was remapped to F2 in Synapse and persists across reboots/replugs without daemon or Synapse running. But Copilot/RWin remap does NOT persist — reverts when Synapse closes. Hypothesis: F-key row has onboard persistent storage (maybe related to multimedia/Fn layer), other keys are volatile only. Need to understand: which remaps persist? Is there a separate "save to onboard" command (one of unexplored class 0x02 SET commands)?
> - **INVESTIGATE: Fn key row defaults to multimedia keys** (F4=WindowArrange, F5=VolMute, F6=VolDown, F7=VolUp, etc). Standard F-keys require Fn modifier. Need to understand how this interacts with remaps and whether there's a command to toggle Fn-lock mode.

## Build 2026-04-10--0130
**Changes**
- **Complete rewrite of keyboard hook architecture** — replaced pending-modifier state machine with modifier gate
- Lock key → Delete: single tap + hold-to-repeat, verified stable
- Copilot key → Ctrl+F12: verified working (triggers Greenshot)
- Win key, Win+E, Start menu all work normally
- Debug logging to file (`hook_debug.log`) for safe hook diagnostics
- `remove_hook()` releases all modifiers and clears state on shutdown
- Cleanup modifier injection on remap completion (LRESULT(1) suppression incomplete)
- Prefix mod cancellation: Copilot's leaked LShift undone at trigger time
- 36 unit tests (2 new for prefix mod detection)

**Bugs Fixed**
- Orphan key-ups from firmware event ordering (Win↑ before L↑)
- Auto-repeat Win↓ leaking through gate during hold-to-repeat
- LRESULT(1) not fully clearing Windows internal key state
- Copilot LShift prefix leaking through → Shift+Ctrl+F12 instead of Ctrl+F12

**Hardware Verification**
- Lock key → Delete: CONFIRMED (single tap, hold-repeat, clean state)
- Copilot key → Ctrl+F12: CONFIRMED (Greenshot screenshot triggered)
- Win key tap → Start menu: CONFIRMED working
- Win+E → Explorer: CONFIRMED working
- Normal typing: CONFIRMED stable

> [!warning] Testing Checklist
> - [x] Lock key → Delete (single tap)
>   - Notes: Working. Requires DisableLockWorkstation registry key.
> - [x] Lock key → Delete (hold to repeat)
>   - Notes: Working. Auto-repeat generates repeated Delete.
> - [x] Copilot key → Ctrl+F12
>   - Notes: Working. Triggers Greenshot. LShift prefix properly cancelled.
> - [x] Win key tap → Start menu
>   - Notes: Working. Gate replays Win tap when no trigger follows.
> - [x] Win+E → File Explorer
>   - Notes: Working. Non-trigger keys replay gate mod immediately.
> - [x] Normal typing stability
>   - Notes: Stable. No modifier corruption after remaps.
> - [ ] USB replug reconnect
>   - Notes: Not tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not tested

## Build 2026-04-09--2359
**Changes**
- Unified pending-modifier state machine replacing hardcoded COMPANION_MODIFIERS
- Multi-candidate trigger matching (LWin → either L or 0x86)
- Scan codes in SendInput via MapVirtualKeyW
- `build_remap_tables` returns both combo and pending-modifier tables
- Config supports `from = "Win+L"` combo-source syntax
- Default config includes Lock→Delete and Copilot→Ctrl+F12 entries
- DisableLockWorkstation registry key for Win+L interception
- 34 unit tests (4 new)

**Hardware Verification**
- Lock key → Delete: CONFIRMED WORKING (multiple sessions)
- Copilot key → Ctrl+F12: BROKEN — SendInput modifier injection causes stuck LCtrl

> [!warning] Testing Checklist
> - [x] Lock key → Delete
>   - Notes: Works reliably. Requires DisableLockWorkstation registry key.
> - [ ] Copilot key → Ctrl+F12
>   - Notes: **BLOCKED.** SendInput Ctrl↓/Ctrl↑ injection corrupts Windows keyboard state. LCtrl gets stuck after 1-4 presses. Required multiple reboots. Need firmware-level approach (find matrix index, remap to F13, then hook F13→Ctrl+F12).
> - [ ] Normal typing stability with daemon running
>   - Notes: Lock-only appears stable. With Copilot remap active, keyboard eventually gets stuck modifiers.
> - [ ] USB replug reconnect
>   - Notes: Not tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not tested

## Build 2026-04-09--1830
**Changes**
- Built complete Rust systray daemon (`joro-daemon`) — 6 modules, 30 unit tests
- `src/keys.rs` — VK code + HID usage lookup tables, combo string parser
- `src/usb.rs` — Razer 90-byte packet builder/parser + RazerDevice USB transport
- `src/config.rs` — TOML config schema, loader, color parser, default creation
- `src/remap.rs` — WH_KEYBOARD_LL keyboard hook + SendInput combo synthesis
- `src/tray.rs` — systray icon (green/grey circle), right-click menu
- `src/main.rs` — winit event loop, device lifecycle, config auto-reload
- Config at `%APPDATA%\razer-joro\config.toml`, auto-created on first run
- Fixed USB interface claiming (claim_interface + auto_detach_kernel_driver)
- Fixed winit event loop exit (hidden window required for systray-only apps)
- Build infrastructure: `build.ps1`, `.cargo/config.toml.example`, local target dir

**Hardware Verification**
- Static color orange (#FF4400) at brightness 200 — confirmed on keyboard
- CapsLock -> Ctrl+F12 host-side combo — confirmed working
- Firmware version query — confirmed
- Systray icon shows connected/disconnected state — confirmed
- Right-click menu (reload, open config, quit) — confirmed

> [!warning] Testing Checklist
> - [x] Static color set from config — observed on keyboard
>   - Notes: Orange (#FF4400) at brightness 200, applied on startup
> - [x] Host-side combo remap (CapsLock -> Ctrl+F12)
>   - Notes: Keyboard hook intercepts CapsLock, synthesizes Ctrl+F12 via SendInput
> - [x] Systray icon and menu
>   - Notes: Green circle when connected, right-click menu works
> - [ ] USB replug reconnect
>   - Notes: Not yet tested this build
> - [ ] Config file auto-reload
>   - Notes: Not yet tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not yet tested

## Build 2026-04-09--2100
**Changes**
- Identified CapsLock matrix index: **30** (confirmed via F12 remap test)
- Exhaustive modifier combo remap testing — **firmware does NOT support combos**
  - Tested: type field variations (01/02, 02/01, 03/02, 07/02, 02/07), modifier in pad/extra bytes, two-entry writes, modifier usage in pad
  - Only 1:1 key swaps work (including remap to modifier alone, e.g., CapsLock -> LCtrl)
  - Combos/macros must be implemented in host software
- Scanned all class 0x02 commands: GET 0x82/0x83/0x87/0x8D/0xA4/0xA8, SET 0x02/0x03/0x07/0x0D/0x24/0x28
- Created `proto/validate_keymap.py` — full keymap dump tool
- Created `proto/find_capslock_v2.py` — batch remap index finder
- Created `proto/map_all_keys.py` — batch-by-batch full keyboard mapper

> [!warning] Testing Checklist
> - [x] CapsLock remap — matrix index 30 confirmed
>   - Notes: Remapped to F12, verified CapsLock triggers F12 (opens devtools)
> - [x] CapsLock -> LCtrl — confirmed working (hold + C copies)
>   - Notes: Simple modifier remap works with usage=0xE0
> - [x] Modifier combo remap (Ctrl+Esc, Ctrl+A) — NOT SUPPORTED
>   - Notes: All entry format variations tested, firmware ignores modifier fields
> - [ ] Sleep/idle config SET command
>   - Notes: Not yet tested
> - [x] BLE GATT exploration
>   - Notes: 6 services found. Custom Razer service `52401523-...` has TX/RX/RX2 characteristics. 20-byte max write (PDU=23). BLE command protocol requires authentication — device returns nonce + status 0x03 for all writes. BLE VID=0x068E PID=0x02CE. Battery=100%. Conn params: 7.5-15ms interval, latency=20, timeout=3s. maintain_connection=False by default.

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
