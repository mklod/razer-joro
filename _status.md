# Razer Joro — Status

## Current milestone
Stage 4: BLE Control — SET brightness and SET color WORKING over BLE via MITM proxy. No Synapse required.

## Last session (2026-04-10 1500–1730) — BLE SET Commands Cracked + Effects Mapped + Rust BLE Module

### Completed
- **SET brightness over BLE — WORKING** — `0x10/0x05 sub1=0x01 data=[brightness]`
- **SET color over BLE — WORKING** — `0x10/0x03 sub1=0x01 data=[01,00,00,01,R,G,B]`
- **RGB color cycling verified** — 3 full R→G→B cycles, visually confirmed on keyboard hardware
- **Firmware version confirmed: v1.2.2.0** (updated from 1.0.4.0)
- **Effect data format decoded** — variable-length: `[effect, param, 0, num_colors, R1,G1,B1, ...]`, dlen = 4 + (num_colors * 3)
- **Static, breathing (1+2 color), spectrum cycling** — all formats captured from Chroma Studio HCI trace
- **Additional driver commands discovered** — SET 0x01/0x02, SET 0x06/0x02 (idle config), more class 0x05 GETs

### Key Discoveries
1. **20-byte padding bug was the "auth gate"** — `central_write_to_keyboard()` padded all writes to 20 bytes. Keyboard requires exact byte lengths (8B for header, 8+N for data). Fixing this made all GETs work on new firmware.
2. **Split write protocol** — SET commands require header and data as TWO SEPARATE ATT Write Requests. Concatenating them into one write returns FAILURE (0x03). Discovered via BT HCI ETW capture of the Razer driver.
3. **sub1=0x01 required** — SET brightness/color need sub-param byte 6 = 0x01. We tried 0x00 and 0x05 — neither worked. Found correct value from HCI capture.
4. **SET color cmd=0x03** (not 0x02) — mirrors GET 0x83 with high bit cleared.
5. **No BLE encryption needed** — SMP pairing fails (PAIR_NOT_ALLOWED) but SET commands work without it. The split write was the only blocker.
6. **Driver init sequence captured** — `0x01/0xA0` (x2), `0x05/0x87`, `0x05/0x84`, `0x05/0x07` SET

### Methods Used
- **BT HCI ETW capture** — `logman` with BTHPORT provider, parsed ETL→XML with `tracerpt`, extracted ATT Write Requests showing split write pattern
- **MITM proxy firmware iterations** — 6 builds testing different hypotheses (padding fix, exact lengths, Write Request vs WwoR, SMP pairing, split writes)
- **Razer driver analysis** — strings extraction from `RzDev_02ce.sys` (67KB, KMDF)

### Working BLE Protocol30 Command Reference
```
GET (single write, 8 bytes):
  [txn, 0, 0, 0, class, cmd, sub1, sub2]

SET (split write: 8-byte header + N-byte data as separate ATT writes):
  Write 1: [txn, dlen, 0, 0, class, cmd, sub1, sub2]
  Write 2: [data bytes...]

Brightness: class=0x10, GET=0x85, SET=0x05, sub1=0x01
Color:      class=0x10, GET=0x83, SET=0x03, sub1=0x01
  Color data: [01, 00, 00, 01, R, G, B] (7 bytes, static effect)
```

## Next immediate task
- Build Python BLE control script using bleak — direct keyboard control without proxy
- **Debug btleplug connection on Windows** — scan doesn't find already-paired devices; first run found+failed to connect, second run didn't find at all. Need to handle Windows paired-device enumeration.
- Test BLE daemon end-to-end (color change via config.toml over BLE)
- Capture wave/reactive/starlight effects from Chroma Studio
- Python bleak direct control script (alternative to btleplug debugging)

## Blockers
- Wave (0x04), reactive (0x05), starlight (0x06) data formats not yet captured
- Brightness change was protocol-confirmed but not visually observed — need re-test

## Key decisions
- Split write protocol is the fundamental mechanism for all BLE SET commands
- No encryption/auth needed — simplifies the control path significantly
- sub1=0x01 appears to be a "profile" or "target" identifier, not VARSTORE
- BLE uses class 0x10 (BLE-native) instead of USB class 0x0F — cmd IDs mirror USB (GET=0x80+, SET=0x00+)
- Effect data is variable-length: dlen = 4 + (num_colors * 3). Static/breathing-1=7B, breathing-2=10B, spectrum=4B
- **BLE key remaps confirmed host-side only** — HCI capture showed zero class 0x02 commands from Synapse; killing Synapse broke the remap instantly. Our WH_KEYBOARD_LL approach is the correct replacement.

## Previous session (2026-04-10 0330–1456) — BLE MITM Proxy + Protocol30 Discovery + FW Update

### Completed
- **Zephyr SDK 0.17.0 installed** — ARM toolchain at `C:\Users\mklod\zephyr-sdk-0.17.0\`
- **Zephyr workspace** — `C:\Users\mklod\zephyrproject\` with west init v4.1.0 + west update
- **CMake 4.3.1, Ninja 1.13.2** installed via winget
- **Zephyr Python deps** installed from `scripts/requirements.txt`
- **BLE MITM proxy firmware** — 4-file Zephyr app (`central.c`, `peripheral.c`, `relay.c`, `main.c`)
  - Builds for `nrf52840dongle/nrf52840` board target
  - Source at `firmware/ble-mitm-proxy/`, build copy at `C:\Users\mklod\zephyrproject\ble-mitm-proxy\`
  - Flash via: `nrfutil device program --firmware .../zephyr.hex --options chip_erase_mode=ERASE_ALL --traits jlink`
- **Upstream (proxy→keyboard) WORKING:**
  - Proxy scans, finds Joro by name, connects as BLE central
  - GATT discovery succeeds: 1524 (write, handle=69), 1525 (notify, handle=71), 1526 (notify, handle=74)
  - Subscribed to notifications on 1525 and 1526
  - Received unsolicited notification: `01 00 00 00 00 00 00 03 2a 65 10 14 47 a7 50 31 e5 f5 60 dd`
  - Byte 7 = 0x03 = "Command Failure" (initially misread as "not authenticated")
  - Bytes 8-19 = 12-byte session/state data (constant per session, changes between sessions)
  - Keyboard stays connected (bonded to proxy MAC, no 2s disconnect)
- **Downstream (Synapse→proxy) PARTIALLY WORKING:**
  - Windows pairs with proxy via BLE (SMP enabled, DIS with VID=0x068E/PID=0x02CE)
  - Synapse detects proxy as "Joro" in device list
  - Windows BLE stack connects/disconnects rapidly (0x13 Remote User Terminated)
  - Synapse never writes to the Razer custom GATT service (1524)
  - Likely missing: HID-over-GATT service — Synapse may require HID before using custom service
- **USB CDC serial logging** on COM12 + SEGGER RTT via J-Link for debug output
- **Bond persistence** via NVS flash storage (`CONFIG_BT_SETTINGS=y`)

### Key Discoveries
- **nRF52840 dongle flash requires FLASH_LOAD_OFFSET=0x0** — board default is 0x1000 (expects MBR bootloader). Without MBR, vector table is empty → HardFault
- **Immediate-mode logging causes stack overflow** in BT RX thread — must use `CONFIG_LOG_MODE_DEFERRED=y`
- **BT RX stack needs 8192 bytes** for BLE central+peripheral with GATT discovery
- **Keyboard BLE pairing slots are exclusive** — 3 slots, only ONE active at a time (OR, not AND). Long-press (5s) clears bond on slot, short-press reconnects. Each slot uses a different MAC (2F:9F, 2F:A2, 2F:A3, 2F:A4, 2F:A5 seen)
- **Keyboard stays connected after bonding** — previous 2s disconnect was due to connecting to already-bonded slots. Fresh slot = stable connection
- **First BLE protocol data captured:** `01 00 00 00 00 00 00 03 ...` on characteristic 1525 — status 0x03 = Command Failure
- **Protocol30 has NO encryption** — Synapse JS source confirms plaintext protocol. 0x03 responses were due to malformed packets, not auth failure

### Build Infrastructure
- Zephyr SDK 0.17.0 at `C:\Users\mklod\zephyr-sdk-0.17.0\`
- Zephyr workspace at `C:\Users\mklod\zephyrproject\` (v4.1.0)
- Build command: `west build -b nrf52840dongle/nrf52840 ble-mitm-proxy --build-dir ble-mitm-proxy/build`
- Flash command: `nrfutil device program --firmware .../zephyr.hex --options chip_erase_mode=ERASE_ALL --traits jlink`
- Must set: `ZEPHYR_SDK_INSTALL_DIR`, `ZEPHYR_BASE`, cmake in PATH

### Protocol30 Discovery (from Synapse source decompilation)
- **Synapse is Electron app** — web modules cached in Service Worker CacheStorage
- **Source found:** `6886.d85cef2c.chunk.js` = `rzDevice30Layla` class (keyboard BLE handler)
- **Packet format:**
  ```
  Byte 0: transactionId (auto-increment)
  Bytes 1-3: data length (24-bit: [0, hi, lo])
  Byte 4: commandClass1
  Byte 5: commandClass2
  Byte 6: commandId
  Byte 7: subCommandId
  Bytes 8+: data payload
  ```
- **Response byte 7 status codes:** 1=BUSY, 2=SUCCESS, 3=FAILURE, 4=TIMEOUT, 5=NOT_SUPPORTED
- **No crypto/auth in JS layer** — Protocol30 is plaintext, the 0x03 status = "Command Failure"
- **Initial probe results with raw bytes:** keyboard echoes byte 0, always returns status 0x03 + 12-byte nonce
- **Protocol30 formatted probe:** no responses received (keyboard had disconnected by then)
- **Key files in Synapse source:**
  - `electron/modules/noble/constants.js` — UUIDs, protocol constants
  - `electron/modules/noble/strategy/protocol30.js` — transport layer
  - `electron/modules/noble/index.js` — BLE connection manager, status code decoder
  - Service Worker cache `6886.d85cef2c.chunk.js` — `rzDevice30Layla` keyboard implementation

### BLE Command Verification (8-byte header probes)
- **GET firmware, battery, device type, power, brightness — all SUCCESS**
- **GET serial, keymap — NOT_SUPPORTED over BLE**
- **All SET commands with data payloads — FAILURE** (command recognized but data format wrong)
- **8-byte header-only = correct format for GET; data payloads need different byte layout than USB**
- **Connecting to proxy from Python:** use `BleakClient(addr)` directly (no scan) since device is paired

### SET Command Testing — Data Payload Issue (pre-FW-update)
- **Header-only (8B) commands worked** — GET firmware, battery, device type = SUCCESS
- **Any command with data (>8B) returned FAILURE (0x03)** regardless of class/cmd/data content
- Tested: BLE-native class IDs (0x10/0x05), USB class IDs (0x0F/0x04), single write, split write, 20B padded, session token echo — ALL fail
- Synapse uses kernel driver (`RzDev_02ce.sys`) + `rzLampArrayChannel` for lighting, not Protocol30 JS

### Firmware Update
- Updated Joro firmware via Razer updater (USB required)
- **DFU PID = `0x110E`** (VID `0x1532`) captured during update
- **New firmware enables BLE lighting in Synapse** — confirmed color changes + on/off toggle work via Synapse over BLE
- **New firmware locks ALL Protocol30 commands behind authentication** — even GET firmware (which worked on old FW) now returns FAILURE (0x03)
- Response suffix changed from `2a e5 10 14 67 a7 71 31 ed f5 60 d9` (old FW session data) to `ff ff ff ff ff ff ff ff ff ff ff ff` (new FW = no session)
- The Razer driver handles session authentication transparently — JS layer never sees it

### Synapse Architecture Discovery
- **Lighting uses `rzLampArrayChannel`** → Windows LampArray API → `RzDev_02ce.sys` kernel driver
- **Driver constructs Protocol30 commands** internally — Synapse JS never builds lighting packets directly
- **Two communication paths:** Protocol30 direct (battery, firmware) vs driver-mediated (lighting, keymaps)
- **Product ID 717** = Joro in Synapse; `project_anne_joro` webpack module
- **Driver file:** `RzDev_02ce.sys` (67KB) handles BLE Protocol30 for lighting/keymaps

## Next immediate task
- **Capture Razer driver's session auth handshake** — the driver (`RzDev_02ce.sys`) authenticates with the keyboard before sending commands. Must capture this:
  - **Option A:** Add HID-over-GATT stub to MITM proxy so driver recognizes it as Joro, connects through it, and we capture the full auth + command sequence
  - **Option B:** Reverse-engineer `RzDev_02ce.sys` with IDA/Ghidra to find the auth algorithm
  - **Option C:** Fix USBPcap on this machine and capture what the driver sends over BLE at the HCI level
- **New firmware version needs to be queried** — GET firmware now fails too, need auth first

## Blockers
- New firmware requires session authentication for ALL Protocol30 commands (including GETs)
- Razer driver handles auth transparently — not visible in Synapse JS layer
- USBPcap broken on this machine — can't passively capture BLE HCI traffic

## Blockers
- Synapse won't use the custom GATT service without (likely) HID-over-GATT present
- `chip_erase_mode=ERASE_ALL` wipes bond storage — need to re-pair after every flash. Consider using `ERASE_RANGES_NEEDED_BY_FIRMWARE` instead

## Key decisions
- `CONFIG_FLASH_LOAD_OFFSET=0x0` — no bootloader, flash via SWD directly
- `CONFIG_LOG_MODE_DEFERRED=y` — prevents stack overflow in BT callbacks
- `CONFIG_BT_MAX_CONN=3` — upstream + downstream + spare for reconnect churn
- Bond persistence via NVS (`CONFIG_BT_SETTINGS=y`)
- Source on L: drive, build on C: drive (west can't handle cross-drive paths)

## Previous session (2026-04-10 0330) — Zephyr SDK 0.17.0 Install (Windows, nRF52840)

### Completed
- **SDK version confirmed:** Zephyr v4.1.0 compatible with SDK 0.17.0 (released 2024-10-20)
- **Downloaded:** minimal bundle (`zephyr-sdk-0.17.0_windows-x86_64_minimal.7z`) + arm toolchain (`toolchain_windows-x86_64_arm-zephyr-eabi.7z`) from GitHub sdk-ng releases
- **Extracted to:** `C:\Users\mklod\zephyr-sdk-0.17.0\` — contains `arm-zephyr-eabi/`, `cmake/`, `sdk_version`, `sdk_toolchains`, `setup.cmd`
- **Toolchain verified:** `arm-zephyr-eabi-gcc.exe (Zephyr SDK 0.17.0) 12.2.0` runs correctly
- **CMake package registered:** `cmake -P cmake/zephyr_sdk_export.cmake` wrote registry key `HKCU\Software\Kitware\CMake\Packages\Zephyr-sdk` — CMake `find_package(Zephyr-sdk)` will auto-locate the SDK without needing `ZEPHYR_SDK_INSTALL_DIR`
- **Env var set:** `ZEPHYR_SDK_INSTALL_DIR=C:\Users\mklod\zephyr-sdk-0.17.0` as persistent user environment variable (backup for tools that need it explicitly)
- **Toolchain used:** 7zr.exe (from 7-zip.org v26.00) for extraction — Git bash has no 7z, and 7-Zip is not installed system-wide

### Key Info
- SDK install path: `C:\Users\mklod\zephyr-sdk-0.17.0\`
- GCC: `arm-zephyr-eabi-gcc` 12.2.0 at `C:\Users\mklod\zephyr-sdk-0.17.0\arm-zephyr-eabi\bin\`
- Zephyr workspace: `C:\Users\mklod\zephyrproject\` (west init --mr v4.1.0)
- CMake: `C:\Program Files\CMake\bin\cmake.exe` v4.3.1

---

## Last session (2026-04-10 0300) — BLE Sniffing Setup + GATT Enumeration

### Completed
- **nRF52840 dongle setup:** Installed nrfutil, ble-sniffer command, Wireshark extcap bootstrap
- **Dongle recovery:** Previous custom firmware had overwritten bootloader. Restored DFU bootloader via J-Link (SWD) + `open_bootloader_usb_mbr_pca10059_debug.hex`, then flashed sniffer firmware via DFU
- **J-Link driver fix:** SEGGER J-Link V9.34a installed, USB driver manually pointed at `C:\Program Files\SEGGER\JLink_V934a\USBDriver`
- **Barrot BLE 5.4 adapter:** Installed driver v17.55.18.936 from MS Update Catalog (extracted .cab, `pnputil -a` + Device Manager manual update)
- **BLE sniffer captures:** 8+ attempts. Captured Joro advertising and CONNECT_IND packets but sniffer marks them as malformed — cannot follow connection onto data channels. Single-radio sniffer insufficient.
- **GATT service enumeration:** Full map via WinRT Python (see `docs/ble-reverse-engineering.md`)
- **Razer BLE protocol discovery:** Custom service `5240xxxx` does NOT use USB 90-byte packet format. MTU=23 (20-byte payload max). Characteristics contain encrypted-looking data. Protocol requires authentication/session setup.
- **BLE command testing:** 20-byte writes accepted by `...1524` char but no valid command response. 90-byte writes fail (MTU too small). USB protocol does not transfer to BLE.

### Key Discoveries
- **BLE protocol is separate from USB** — different packet format, likely encrypted proprietary channel
- **Keyboard works over BLE without Synapse** — standard HID-over-GATT handles input natively
- **Joro BLE MAC rotates** on each pairing (seen a0, a1, a2, a3 suffixes)
- **Barrot BLE 5.4 adapter** works but doesn't negotiate MTU above 23
- **Intel onboard BT** in Error state in Device Manager, unusable

### Tools Installed This Session
- nrfutil v8.1.1 at `C:\Users\mklod\bin\nrfutil.exe`
- nrfutil ble-sniffer v0.18.0 + nrfutil device
- SEGGER J-Link V9.34a at `C:\Program Files\SEGGER\JLink_V934a\`
- Barrot BLE 5.4 driver v17.55.18.936
- Python bleak package

### Scripts Created
- `scripts/ble_gatt_enum.py` — enumerate Joro GATT services via WinRT
- `scripts/ble_test_command.py` — BLE command testing (failed due to protocol mismatch)

## Next immediate task
- **BLE MITM proxy** — flash nRF52840 with GATT proxy firmware to intercept Synapse↔Joro BLE traffic in plaintext. Option 1 (sniffer) failed; Option 2 (MITM) is the path forward.
- See `docs/ble-reverse-engineering.md` for full plan

## Blockers
- BLE protocol unknown — need MITM capture of Synapse session to reverse-engineer
- Intel BT adapter broken (not critical, Barrot works)

## Key decisions
- Single-radio BLE sniffer cannot reliably capture connection traffic — MITM proxy required
- BLE custom service uses different protocol than USB (not just MTU-limited)
- Barrot BLE 5.4 adapter is primary BT adapter going forward

---

## Previous session (2026-04-10 0230) — Autostart + Persistent Storage Investigation

### Completed
- **Autostart toggle** — tray menu "Autostart: On/Off", writes `HKCU\...\Run\JoroDaemon` registry key
- **Persistent remap storage investigation** — CONCLUDED: not available for arbitrary keys
  - Probed all class 0x02 SET candidates (0x02, 0x03, 0x07, 0x0D, 0x28) with size=0 after volatile keymap write — none made remap survive replug
  - Varstore prefix (0x01 byte before entry) — firmware didn't recognize format
  - Probed classes 0x03, 0x04, 0x05 GET commands — no storage/save commands found
  - Class 0x04 has 48 GET commands (0x80-0xAF) all returning size=0 — possibly empty macro/profile slots
- **Lighting persistence confirmed** — SET 0x0F/0x02 (static color) auto-persists across USB replug. Firmware stores lighting state permanently without explicit save command.
- **Python USB transport broken for keymap writes** — pyusb ctrl_transfer no longer writes keymaps after replug. Rust daemon (rusb with claim_interface) still works. Root cause unclear.

### Key Discoveries
- **Lighting: auto-persistent.** Color/brightness survive replug. Class 0x0F writes to non-volatile storage automatically.
- **Keymaps: always volatile.** Class 0x02/0x0F writes reset on replug. No save command found. Daemon must re-apply on every connect (which it already does).
- **F-key persistent remaps (Synapse):** likely use a separate firmware mechanism for the multimedia/Fn layer, not the general keymap API. Or Synapse re-applies on startup like our daemon.
- **Fn key row defaults to multimedia** (volume, mute, etc). Fn modifier enables actual F-keys. Fn hold makes backlight solid white.

## Next immediate task
- Test 2.4GHz dongle (PID 0x02CE)
- Release build + single exe packaging
- Map more keyboard matrix indices

## Blockers
- None

## Key decisions
- Persistent keymap storage not available — daemon re-applies on connect (correct approach)
- Autostart via registry Run key (not Startup folder)

---

## Previous session (2026-04-10 0130) — Modifier Gate: Both Remaps Working

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

