# Razer Joro — Status

## Current milestone
Stage 3: Rust Daemon — Lock→Delete and Copilot→Ctrl+F12 both WORKING. Modifier gate architecture verified on hardware.

## Last session (2026-04-10 0130) — Modifier Gate: Both Remaps Working

### Completed
- **Complete rewrite of hook architecture** — replaced old pending-modifier state machine with "modifier gate" approach
- **Lock key → Delete: WORKING** (single tap + hold-to-repeat)
- **Copilot key → Ctrl+F12: WORKING** (triggers Greenshot screenshot)
- **Win key, Win+E, Start menu all work normally** — gate resolves on next keypress, non-trigger keys replay immediately

### Architecture: Modifier Gate
- On gate modifier keydown (Win): suppress it, wait for next key
- Trigger key (L, 0x86): fire remap, enter active trigger state
- Non-trigger key (E, D, ...): replay gate mod immediately, pass key through
- Gate mod key-up (Win tap): replay full tap → Start menu works
- Two-phase release: active trigger stays alive until BOTH trigger↑ and gate↑ arrive
- Auto-repeat: gate mod repeats suppressed, trigger repeats → output key repeats
- Cleanup: inject gate mod key-up on completion (LRESULT(1) suppression doesn't fully clear Windows internal key state)
- Prefix mods: Copilot sends LShift↓ before LWin↓; LShift leaks through pre-gate, cancelled with injected key-up when trigger fires

### Bugs Found & Fixed
1. **Orphan key-ups**: firmware sends Win↑ before L↑ (not L↑ before Win↑ as assumed). Two-phase release needed.
2. **Auto-repeat Win↓ leaked through gate**: during hold-to-repeat, Win↓ repeats fell through to gate logic, got replayed as injected Win↓ without matching Win↑. Fixed by suppressing gate_vk repeats in active trigger.
3. **LRESULT(1) doesn't fully clear Windows key state**: suppressing Win↓ and Win↑ left Windows thinking Win was stuck. Fixed by injecting cleanup Win↑ on completion.
4. **Copilot LShift prefix leaked**: LShift↓ arrives before LWin↓ (gate mod), passed through uncaught. Fixed by injecting LShift↑ when trigger fires.
5. **Injected keys don't prevent Start menu**: Windows ignores LLKHF_INJECTED events for Start menu detection. Dummy key approach (scancode 0xFF) failed.

### Key Discovery
- **Windows Start menu detection ignores injected events** — only hardware (non-LLKHF_INJECTED) keys between Win↓ and Win↑ prevent Start menu from triggering. Must suppress Win↓ entirely, not try to cancel it after the fact.
- **Firmware macro event ordering**: Lock key sends Win↓, L↓, but key-ups arrive as Win↑ THEN L↑ (not L↑ then Win↑). SendInput during hook processing may also reorder events.

## Next immediate task
- Commit the working state
- Disable debug logging infrastructure (file logger can be removed or kept behind flag)
- Test 2.4GHz dongle (PID 0x02CE)
- Add Windows autostart registration
- Release build + single exe packaging

## Blockers
- None

## Key decisions
- Modifier gate > trigger-based interception > pending-modifier state machine (each approach failed and informed the next)
- Debug logging to file (`hook_debug.log`) instead of stderr to avoid hook timeout removal
- `DisableLockWorkstation` registry key required for Lock→Delete
- Prefix mods (LShift for Copilot) cancelled at trigger time, not pre-gate (can't know if gate will follow)

---

## Previous session (2026-04-09 2351) — Combo-Source Remaps: Lock Works, Copilot Blocked

### Completed
- **Lock key → Delete: WORKING.** Requires `DisableLockWorkstation` registry key (Win+L is OS-protected, can't be intercepted by WH_KEYBOARD_LL or RegisterHotKey).
  - Registry: `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System\DisableLockWorkstation = 1`
- **Unified pending-modifier state machine** — replaced hardcoded `COMPANION_MODIFIERS` with data-driven `PendingModifierRemap` table supporting multiple triggers per held modifier
- **Multi-candidate trigger matching** — when LWin is pressed, waits for EITHER L (Lock) or 0x86 (Copilot)
- **Scan codes in SendInput** — `MapVirtualKeyW` populates `wScan` field
- **Config format** — `from = "Win+L"` combo-source syntax works in TOML
- 34 unit tests pass (4 new for combo-source parsing)
- 4 commits: scan codes, unified state machine, build_remap_tables, wiring

### IN PROGRESS: Copilot key → Ctrl+F12
- **Root cause is NOT SendInput modifier injection** — CapsLock→Ctrl+F12 uses the exact same SendInput approach and works perfectly. The proven ComboRemap path (single key → combo output) is reliable.
- **Actual root cause: Copilot key's LShift prefix leaks through.** Copilot sends LShift↓, LWin↓, 0x86↓. LShift passes through to the system BEFORE LWin enters the pending state. This leaked LShift combines with our injected Ctrl+F12, creating Shift+Ctrl+F12 and corrupting modifier state.
- **Secondary issue: key-up ordering.** Copilot releases LWin↑ before 0x86↑ (opposite from Lock key's L↑ then LWin↑). Earlier code assumed trigger↑ before held↑.
- Approaches tried:
  1. **Companion modifier state machine** — single trigger per held VK. Failed: only matched first table entry when multiple remaps share held_vk. Fixed with multi-candidate.
  2. **RegisterHotKey** — Win+L fails (OS-protected). Win+0x86 conflicts with hook (double fire).
  3. **release_stray_modifiers via GetAsyncKeyState** — injected unpaired modifier key-ups that accumulated.
  4. **Tap-style output** — correct direction but LShift leak still corrupts state.
- **LShift↑ injection on pending entry** — tried injecting LShift↑ when LWin enters pending. Still caused stuck modifiers. The held-style combo (combo_down on confirm, combo_up on first key-up) also failed.
- **Next approach:** Find Copilot firmware matrix index → remap to single key (F13) at hardware level → use proven ComboRemap path (F13→Ctrl+F12, same as working CapsLock remap). This completely avoids the LShift+LWin prefix problem.

### Key Protocol Discoveries This Session
- **Copilot key actual sequence:** LShift↓, LWin↓, VK 0x86↓, VK 0x86↑, LWin↑, LShift↑ (scan=0x006E)
- **Lock key sequence:** LWin↓, L↓, L↑, LWin↑ (standard firmware macro)
- **Win+L is OS-protected:** Cannot be intercepted by WH_KEYBOARD_LL (hook sees events but LRESULT(1) doesn't prevent lock). Cannot be claimed by RegisterHotKey. Only `DisableLockWorkstation` registry works.
- **WH_KEYBOARD_LL hook fragility:** Windows removes hooks that don't respond within ~300ms (LowLevelHooksTimeout). Per-key debug logging (eprintln + Mutex locks) may trigger this.

### Safety Lessons
- **MUST have in-daemon watchdog** that periodically releases all modifiers if no key activity for N seconds — auto-exit timeout doesn't help when keyboard is stuck and can't type "kill"
- **Never inject unpaired modifier events** — GetAsyncKeyState + inject key-up is unreliable with injected events
- **Test with safety net first** — remote control access should be set up BEFORE testing keyboard hooks

## Next immediate task
- **Ship Lock→Delete only** — commit current working state, remove Copilot remap from default config
- **Find Copilot matrix index** — run Python key mapper to discover if Copilot key has a firmware-remappable index
- **Add modifier watchdog** — periodic check in daemon that releases all modifiers if they've been held too long without key events
- Test 2.4GHz dongle (PID 0x02CE)

## Blockers
- Copilot→Ctrl+F12 via SendInput modifier injection is fundamentally broken
- Need Copilot key matrix index for firmware-level approach

## Key decisions
- `DisableLockWorkstation` registry key required for Lock→Delete
- SendInput modifier injection (Ctrl↓/Ctrl↑) corrupts Windows keyboard state — avoid for any remap that outputs modifier combos
- Single-key output remaps (like Delete) work reliably via SendInput
- CapsLock→Ctrl+F12 worked in earlier session because CapsLock has no preceding modifiers — the Copilot key's LShift+LWin prefix is what makes it intractable

---

## Previous session (2026-04-09 late) — Rust Daemon MVP + Key Identification

### Completed
- Built full Rust systray daemon (`joro-daemon`) — 6 source modules, 30 unit tests
- **Hardware verified:** lighting (static color + brightness), firmware query, host-side combo remaps
- CapsLock -> Ctrl+F12 confirmed working via WH_KEYBOARD_LL keyboard hook
- Auto-reconnect on USB replug, config file auto-reload on change
- Systray icon with connected/disconnected state, right-click menu (reload, open config, quit)
- Fixed: USB interface claiming, hidden winit window for event loop, KEYEVENTF_EXTENDEDKEY only for extended keys
- Config at `%APPDATA%\razer-joro\config.toml`

### Key Identification
- **Copilot key** = sends LWin (0x5B) + VK 0x86. NOT RWin as initially assumed.
  - Companion modifier state machine: holds LWin, waits for 0x86, suppresses both
  - Intercept works (search no longer opens), but SendInput combo not reaching apps — TODO
- **Lock key** = firmware macro sending LWin+L. Indistinguishable from manual Win+L. Not remappable without intercepting all Win+L.
- **Persistent remaps:** F-key row remaps persist on-device (Synapse-written). Modifier/special key remaps are volatile. Fn row defaults to multimedia keys (F4=arrange, F5=mute, F6=vol-, F7=vol+).

### Build Infrastructure
- Rust 1.94.1 + MSVC 14.44, VS Build Tools 2022
- `build.ps1` wrapper for MSVC env setup
- Target dir: `C:\Users\mklod\AppData\Local\razer-joro-target` (local, not SMB)
- `.cargo/config.toml` excluded from git (machine-specific)

---

## Previous session (2026-04-09 evening) — Keymap Validation + BLE Exploration

### Completed
- **CapsLock matrix index identified: 30** — confirmed via remap-to-F12 test
- **Modifier combo remap testing** — exhaustively tested all entry format variations:
  - Simple key-to-key remap: WORKS (type=02/02, any HID usage including modifiers)
  - CapsLock -> LCtrl: WORKS (usage=0xE0)
  - Modifier+key combos (e.g., Ctrl+Esc): NOT SUPPORTED by firmware
  - Tested: modifier in pad byte, extra bytes, type fields (01/02, 02/01, 03/02, 07/02, 02/07, 02/03), two-entry writes, modifier usage in pad — none produce combos
- **Conclusion:** Firmware only does 1:1 key swaps. Modifier combos must be implemented in host software (intercept + synthesize).
- Created `proto/validate_keymap.py` and `proto/find_capslock_v2.py`
- Scanned all class 0x02 GET/SET commands:
  - GET: 0x82, 0x83, 0x87(2b), 0x8D(5b), 0x8F(keymap), 0xA4(returns 0x02...), 0xA8
  - SET: 0x02, 0x03, 0x07, 0x0D, 0x0F(keymap), 0x24(status=0x03), 0x28

### Key Protocol Discovery
- **Keymap entry format:** `[idx, 0x02, 0x02, 0x00, hid_usage, 0x00, 0x00, 0x00]`
  - type1/type2/pad/extra fields are ignored by firmware — only idx and usage matter
  - Modifier keys (0xE0-0xE7) work as single-key remaps
  - No combo/macro support in firmware keymap entries
- **GET keymap (0x02/0x8F):** Always returns first 8 entries only, pagination broken/unsupported
- **Known matrix indices:** 1=Grave, 2-8=digits 1-7, 30=CapsLock

### BLE Exploration
- **BLE address:** C8:E2:77:5D:2F:9F
- **BLE VID/PID:** 0x068E / 0x02CE (different from USB 0x1532/0x02CD!)
- **Connection params:** interval 7.5-15ms, slave_latency=20, supervision_timeout=3.0s
- **Max PDU:** 23 bytes (20-byte max GATT write payload)
- **GATT services:**
  - Generic Access, Generic Attribute, Device Info, Battery (100%)
  - HID over GATT (0x1812) — access denied (Windows locks this)
  - **Custom Razer service: `52401523-f97c-7f90-0e7f-6c6f4e36db1c`**
    - `...1524` — write (TX: command channel)
    - `...1525` — read+notify (RX: response channel, 20 bytes)
    - `...1526` — read+notify (RX2: secondary channel, 8 bytes)
- **BLE command protocol:** NOT same as USB. Device accepts 20-byte writes but returns
  `[echo_byte0, 00*6, 03, <12-byte nonce>]` — byte 7=0x03 likely means "not authenticated"
- **Conclusion:** Razer BLE protocol requires authentication handshake before commands.
  Synapse handles this. Reversing would need BLE traffic capture from Synapse session.
- **Sleep fix approach:** `maintain_connection=False` in WinRT GattSession — setting to True
  may reduce reconnect delay. Also, supervision_timeout=3s could be tuned.

---

## Previous session (2026-04-09) — Tasks 1-6 (partial) + Protocol RE

### Completed
- Created full Python prototype: `razer_packet.py`, `usb_transport.py`, `commands.py`, `test_lighting.py`, `enumerate.py`
- Cloned openrazer PR branch to `openrazer-ref/`
- Installed Wireshark + USBPcap (USBPcap device nodes broken on this system, not needed)
- **Verified lighting control on hardware** — static color + brightness confirmed
- **Brute-force command scan** — discovered all supported command class/id pairs
- **Key remapping confirmed working** — SET 0x02/0x0F changes keymap, verified backtick->A remap on hardware
- CapsLock index search in progress (confirmed in range 20-35, currently mapped to unique F-keys for identification)

### Major Protocol Discoveries
1. **Transport:** pyusb raw USB control transfers required (hidapi feature reports return 0x05)
   - SET_REPORT: bmRequestType=0x21, bRequest=0x09, wValue=0x0300, wIndex=0x03
   - GET_REPORT: bmRequestType=0xA1, bRequest=0x01, wValue=0x0300, wIndex=0x03
2. **Packet struct:** openrazer `razer_report` layout
   - status@0, txn_id@1(0x1F), remaining_pkts@2-3, proto@4, data_size@5, class@6, cmd@7, args@8-87, crc@88, reserved@89
3. **LED constants:** VARSTORE=0x01, BACKLIGHT_LED=0x05
4. **Lighting commands (verified on hardware):**
   - Static color: class=0x0F id=0x02 size=9 args=[01,05,01,00,00,01,R,G,B]
   - Set brightness: class=0x0F id=0x04 size=3 args=[01,05,brightness]
   - Get brightness: class=0x0F id=0x84 size=1 args=[01] -> resp [01,05,brightness]
   - Get firmware: class=0x00 id=0x81 size=0 -> resp [major,minor]
5. **Key remapping (verified on hardware):**
   - GET keymap: class=0x02 cmd=0x8F — returns 10-byte header + 8x8-byte entries per page
   - SET keymap: class=0x02 cmd=0x0F — write entries, format: `[idx, 0x02, 0x02, 0x00, target_usage, 0x00, 0x00, 0x00]`
   - Entry index is keyboard matrix position (NOT HID usage ID)
   - Known indices: 1=backtick, 2-8=digit keys 1-7
   - After SET, no GET_REPORT response (IO error) — must send-only, then read separately
   - USB replug resets all remaps to defaults
6. **Idle/sleep config (discovered, not yet tested):**
   - GET: class=0x06 cmd=0x86 -> [00,00,00,64,00,00,00,00,00,04,A0,00] (0x64=100, 0x04A0=1184)
   - GET extended: class=0x06 cmd=0x8E -> 14 bytes with duplicate timing values
7. **Full supported command map:** classes 0x00,0x02,0x03,0x04,0x05,0x06,0x07,0x0A,0x0F all have supported commands

### Venv & Environment
- Python venv: `C:/Users/mklod/AppData/Local/razer-joro-venv` (not on SMB share)
- Must kill `razer_elevation_service.exe` before USB access
- libusb DLL: bundled via `pip install libusb`
- Deps: hidapi, bleak, pyusb, libusb

## Last session (2026-04-09) — Task 1: Rust scaffold

### Completed
- Created `Cargo.toml` with all deps: rusb, tray-icon, winit, windows, toml, serde, image
- Created `src/main.rs` + empty module stubs: usb, config, remap, keys, tray
- Created `config.example.toml`
- Installed Rust (rustc 1.94.1, MSVC toolchain) — was not previously installed
- Installed VS Build Tools 2022 with C++ workload (MSVC 14.44.35207, WinSDK 10.0.26100.0)
- Resolved two build blockers:
  1. Git's `link.exe` shadowing MSVC's — fixed via explicit linker path in `.cargo/config.toml`
  2. SMB source drive blocks build script execution — fixed via `build.target-dir` pointing to local AppData
- Created `build.ps1` wrapper script that sets MSVC/WinSDK env vars for clean builds
- Created `.cargo/config.toml.example` (machine-specific config excluded from git)
- `cargo build` verified: Finished in ~37s, 0 errors
- Committed as `feat: scaffold joro-daemon Rust project`

### Key decisions
- `.cargo/config.toml` excluded from git (machine-specific paths); `.cargo/config.toml.example` committed instead
- Build target dir: `C:\Users\mklod\AppData\Local\razer-joro-target` (local, not on SMB)
- Build wrapper: use `.\build.ps1` instead of raw `cargo build`

## Last session (2026-04-09) — Task 2: Key lookup tables

### Completed
- Implemented `src/keys.rs` — full VK/HID lookup tables using `std::sync::LazyLock` HashMaps
- Covers: A-Z, 0-9, F1-F12, navigation keys, punctuation, modifiers (L/R), App key
- Functions: `key_name_to_vk()`, `key_name_to_hid()`, `parse_key_combo()`, `parse_single_hid_key()`
- All 15 specified tests pass (TDD: tests written first, then implementation)
- Committed: `feat: key lookup tables — VK codes, HID usages, combo parsing`

## Last session (2026-04-09) — Tasks 3 & 4: Packet builder/parser + USB device communication

### Completed
- Implemented `src/usb.rs` — full Razer packet builder, parser, and RazerDevice USB API
- **Task 3: Packet builder/parser (TDD)**
  - `build_packet()` — constructs 90-byte Razer packet with CRC (XOR bytes 2..87)
  - `parse_packet()` — decodes response packet, validates CRC
  - `ParsedPacket` struct with status, transaction_id, data_size, command_class, command_id, args, crc_valid
  - All 9 unit tests pass (test_build_packet_size, header, args, crc, parse_roundtrip, bad_crc, get_firmware, set_static_color, keymap_entry)
  - Committed: `feat: Razer packet builder and parser with CRC`
- **Task 4: USB device communication**
  - `RazerDevice::open()` — scans all USB devices for Joro WIRED (0x02CD) and DONGLE (0x02CE) PIDs
  - `send_receive()` — SET_REPORT control transfer + 20ms sleep + GET_REPORT
  - `send_only()` — SET_REPORT + 20ms sleep (for keymap SET which has no GET_REPORT response)
  - `get_firmware()`, `set_static_color()`, `set_brightness()`, `set_keymap_entry()`, `is_connected()`
  - `ConnectionType` enum (Wired/Dongle)
  - Verified compiles clean (warnings only, no errors)
  - Committed: `feat: USB device communication — open, send/receive, lighting, keymap`

## Last session (2026-04-09) — Tasks 6, 7, 8: Host hook, Tray, Main loop

### Completed
- **Task 6: `src/remap.rs`** — WH_KEYBOARD_LL hook engine
  - `build_remap_table()` — filters config remaps; host-side only for combos with `+`
  - `install_hook()` / `remove_hook()` — WH_KEYBOARD_LL via `SetWindowsHookExW`
  - `hook_proc` — injected-event guard (LLKHF_INJECTED bit 0x10), suppresses mapped keys
  - `send_combo_down/up()` — builds INPUT array (modifiers then key), calls `SendInput`
  - Workaround: `SendHook` wrapper to make `HHOOK` (raw pointer) `Send`-safe for `Mutex`
  - Compiles clean (warnings only)
- **Task 7: `src/tray.rs`** — systray icon + menu
  - `create_icon()` — programmatic 32x32 RGBA circle (green=connected, grey=not)
  - `JoroTray::new()` — status/firmware (disabled), separator, reload/open-config/quit
  - `JoroTray::set_connected()` — updates icon + menu text
  - `poll_menu_event()` — non-blocking `try_recv()`
  - Compiles clean
- **Task 8: `src/main.rs`** — full event loop
  - `App` struct: tray, device, config, paths, poll timers
  - `ApplicationHandler` impl: `resumed` (create tray, install hook, connect), `about_to_wait` (2s device poll, 5s config poll, 100ms WaitUntil)
  - `apply_config()` — sets color, brightness, firmware keymaps (skips `+` combos)
  - `check_device()` — polls `is_connected()`, reconnects if lost
  - `check_config_changed()` — mtime compare, auto-reload
  - `handle_menu_events()` — quit/reload/open-config
  - Compiles clean, all 30 tests pass

### One API adaptation needed
- `HHOOK` is `!Send` (wraps `*mut c_void`) — added `SendHook` newtype with `unsafe impl Send` to allow `Mutex<Option<SendHook>>` as a static

## Last session (2026-04-09 night) — Tasks 2+3: Unified pending-modifier state machine

### Completed
- Replaced `COMPANION_MODIFIERS`/`PendingCompanion` hardcoded types with data-driven `PendingModifierRemap` struct and `PendingState` machine
- Added `PENDING_MOD_TABLE` and `PENDING_STATE` statics
- Added `update_pending_mod_table()` public API
- Rewrote `hook_proc` to use table-driven pending-modifier logic (handles any modifier+trigger combo, not just Copilot)
- Added `get_confirmed_trigger()` helper to look up trigger VK from table during key-up handling
- "Confirmed" state uses `trigger_vk = 0` as sentinel — cleaner than a separate bool field
- All 30 tests pass, build clean
- Commit: `fb1f870`

## Last session (2026-04-09) — Tasks 5, 6, 7: Wire up + config defaults + dead code

### Completed
- **Task 5 (main.rs):** Both `resumed()` and `reload_config()` now call `build_remap_tables()` and populate both `REMAP_TABLE` and `PENDING_MOD_TABLE`
- **Task 6 (config.rs):** `DEFAULT_CONFIG` now ships with `Win+L → Delete` and `Win+Copilot → Ctrl+F12` remaps enabled by default; test updated to assert 2 entries
- **Task 6 (live config):** `%APPDATA%\razer-joro\config.toml` updated — replaced old `CapsLock→Ctrl+F12` + `Copilot` (wrong, non-combo form) entries with new `Win+L→Delete` and `Win+Copilot→Ctrl+F12`
- **Task 7 (remap.rs):** Removed `build_remap_table()` compatibility shim (dead code); `COMPANION_MODIFIERS`/`PendingCompanion`/`PENDING_COMPANION` were already gone
- All 34 tests pass, build clean
- Commit: `64c3951`

## Next immediate task
- **Hardware verification:** Run daemon, test Win+L intercept (should → Delete, not lock screen) and Copilot key (should → Ctrl+F12, not Copilot panel)
- **FIX: Copilot key combo output** — previously intercepted correctly but SendInput combo didn't reach apps. New state machine may behave differently — needs re-test.
- Test 2.4GHz dongle (PID 0x02CE) — should work with same USB protocol
- Map more keyboard indices (only 1-8 + 30 known)
- Add Windows autostart registration
- Release build + single exe packaging
- Investigate persistent vs volatile firmware remaps (F-key row persists, others don't)

## Blockers
- None (USBPcap not needed, brute-force scan + direct command testing works)

## Key decisions
- Skipped Wireshark sniffing — brute-force command scan + direct testing more efficient
- pyusb + libusb (not hidapi) for all USB communication
- openrazer PR #2683 as reference for lighting commands
- Razer services must be killed before device access
- Key remap uses matrix index, not HID usage — need index mapping table
- USB replug resets remaps (no onboard storage for custom maps)
- Firmware only supports 1:1 key swaps — combos/macros must be host-side
- CapsLock = matrix index 30

