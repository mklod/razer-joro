# Razer Joro — Status

## Current milestone
Stage 3: Rust Daemon — MVP complete and verified on hardware. Systray app with USB lighting, firmware remaps, and host-side combo remaps (CapsLock->Ctrl+F12 working). Next: dongle testing, more key index mapping, config polish.

## Last session (2026-04-09 late) — Rust Daemon MVP + Key Identification

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

## Next immediate task
- **FIX: Copilot key combo output** — companion state machine intercepts LWin+0x86 correctly, but SendInput combo doesn't reach target app. Likely needs scan codes via MapVirtualKeyW or different SendInput approach. CapsLock→Ctrl+F12 works fine (no companion involved).
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
