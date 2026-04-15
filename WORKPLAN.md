# Razer Joro — Synapse Replacement Workplan

## Project Summary
Lightweight Windows background process to replace Razer Synapse for the Razer Joro keyboard (VID/PID `1532:02CD`). Covers backlight control, key remapping, and BLE sleep fix. Python prototype phase for RE, then Rust production service with systray + webview UI.

**Synapse parity target** (user-defined 2026-04-13): full feature parity MINUS gaming features (keyswitch optimization, scroll wheel, macros). In-scope: fn/mm primary toggle, full Hypershift per-key remap UI, per-key MM override UI, lighting, settings persistence.

**Known hard BLE limits** (verified 2026-04-13, not fixable host-side):
- **F1/F2/F3 = BLE slot selectors**. Firmware-locked. Synapse itself cannot override them in BLE mode (tested with Function Keys Primary on). UI renders them as solid light-grey "locked" keys on BLE transport.
- In **wired** mode, F1/F2/F3 *do* reach the hook and can be remapped host-side. Synapse's fn-primary uses this same mechanism.

**Fn↔MM toggle solved (2026-04-15):**
- Single BLE Protocol30 write: `SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0]`. `mode=0x03` = Fn-primary, `mode=0x00` = MM-primary. GET form `class=0x01 cmd=0x82`. The daemon auto-selects the mode based on the user's config (MM when Win-modified trigger remaps exist, Fn otherwise). Eliminates the rzcontrol filter-driver path entirely. Full write-up in `memory/project_fnmm_toggle_solved.md`.
- **Per-key F4-F12 programmability** follows: in Fn mode F4-F12 emit plain VK_F4..VK_F12 scancodes the LL hook swallows; in MM mode F5/F6/F7/F12 still work via their media VK aliases (VolumeMute/VolumeDown/VolumeUp/Snapshot). F8/F9 in MM mode still need a consumer-hook fallback — `src/consumer_hook.rs` interception is queued.
- **Copilot regression from earlier sessions is gone** — the daemon's auto-detect keeps firmware in MM whenever Lock/Copilot trigger remaps are present, so the hardware combos that Windows needs to generate Win+L / LShift+LWin+0x86 are still emitted by the keyboard.

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
- [x] Map CapsLock matrix index (confirmed: idx 30)
- [x] Test modifier combo remaps — firmware only supports 1:1 swaps, combos need host software
- [x] Map remaining keyboard matrix indices — **~60 keys mapped** via 5-batch `scan` subcommand on 2026-04-13. Full number/tab/caps/shift/nav rows + F1..F12 confirmed. Remaining gaps: 0x3F, 0x41..0x45, 0x52, 0x57, 0x58, >0x7B
- [ ] Decode and test sleep/idle config (class 0x06)
- [x] BLE GATT exploration — services enumerated, custom Razer service found
- [x] BLE Protocol30 reverse engineering — split write protocol cracked, SET brightness/color WORKING
- [ ] ~~Capture Synapse key remap traffic~~ Not needed — direct command testing works
- [ ] ~~Capture Synapse idle/power config~~ Not needed — commands discovered via scan

### Stage 3: Rust Core (Transport + Config) — `IN PROGRESS`
- [x] Implement `RazerPacket` builder with CRC (Rust)
- [x] Implement USB transport via `rusb` (control transfers, interface claiming)
- [x] Config loader (TOML) with `%APPDATA%\razer-joro\config.toml`
- [x] **Host-side combo remap engine** (WH_KEYBOARD_LL + SendInput)
- [x] Systray icon + menu (tray-icon + winit)
- [x] Device lifecycle (auto-reconnect, config reload)
- [x] Hardware verified: lighting, firmware query, CapsLock->Ctrl+F12 combo
- [x] **Lock key → Delete** (combo-source remap via DisableLockWorkstation + modifier gate)
- [x] **Copilot key → Ctrl+F12** (modifier gate + prefix mod cancellation)
- [x] Windows autostart (registry Run key toggle in tray menu)
- [x] Persistent remap storage — investigated, not available (keymaps volatile, lighting auto-persists)
- [ ] Test 2.4GHz dongle (PID 0x02CE)

### Stage 4: BLE + Dongle Transports — `MOSTLY COMPLETE`
- [x] BLE Protocol30 reverse engineering — split write protocol, all 3 bugs found and fixed
- [x] MITM proxy firmware (Zephyr on nRF52840) — full command relay + test harness
- [x] BLE SET brightness verified on hardware (0x10/0x05 split write)
- [x] BLE SET static color verified on hardware (0x10/0x03 split write, RGB cycling)
- [x] BT HCI capture infrastructure (ETW + tracerpt XML parsing)
- [x] BLE effects decoded (static, breathing 1+2 color, spectrum) — variable-length data format
- [x] Key remaps confirmed host-side only over BLE — no Protocol30 needed, WH_KEYBOARD_LL is correct
- [x] Python bleak direct control script (`scripts/ble_direct_control.py`)
- [x] **Rust BLE transport via direct WinRT** — replaced btleplug with direct `windows` crate calls; handles MaintainConnection, paired-device enumeration, clean Drop
- [x] Transport abstraction — `JoroDevice` trait (`src/device.rs`); USB + BLE behind a single `Box<dyn JoroDevice>` field in `App`
- [x] Battery reading on both USB and BLE
- [x] **Fn-layer firmware keymap programming** — reverse-engineered from Synapse capture, working
- [ ] Map remaining effects (wave, reactive, starlight — need HCI capture)
- [ ] Dongle transport (PID 0x02CE — may use USB HID or hybrid)
- [ ] BLE idle/sleep config (SET 0x06/0x02 sub=00,08)

### Stage 5: Systray + WebView UI — `IN PROGRESS`
- [x] Systray via tray-icon + winit (left click → settings, right click → menu)
- [x] Tray icon: pixel-drawn keyboard outline (white when connected, grey + red LED when disconnected)
- [x] Tray menu submenus: Color (8 presets), Brightness (4 levels), Effect (3 modes) with checkmarks
- [x] Tray connect status / firmware / transport indicator
- [x] Webview settings window via wry — fixed size, persisted position
- [x] Visual 75% Joro keyboard with inline SVG icons (BT, screens, speaker, sun, backlight, lock, copilot, windows, globe, media, arrows)
- [x] Per-key alignment variants (top-center default, top-left for Tab/Caps/LShift, top-right for Enter/Backspace, center-center for F-row + arrows)
- [x] Click-to-remap popover with editable From/To, single-key + combo support, exact-match remap engine
- [x] Lighting controls in settings window (color picker + brightness slider + effect dropdown, single row)
- [x] Battery indicator (icon + percent) in window header, updates every 30s
- [x] Auto-save (no Save button)
- [x] **Hypershift (Fn-layer) view** in settings window — toggle between Default and Hypershift, click a key in Hypershift mode to assign Fn+X → Y at the firmware level
- [x] **Action DSL for remap `to` field** — `NA`, `Brightness+Down/Up/±N/=N`, `Backlight+Down/Up/±N/=N`, plus existing media VKs. Parser + hook dispatch landed 2026-04-15.
- [x] **F1/F2/F3 visually locked on BLE** — `.key.ble-locked` CSS + render logic
- [x] **Firmware mode awareness** — webview reads `firmware_fn_primary` from daemon state, remap popover prefill + tooltips switch between Fn/MM based on current mode
- [ ] Visual keyboard polish iteration

### Stage 6: Polish & Packaging — `TODO`
- [x] Autostart registration (registry Run key, toggle in tray menu)
- [x] First-run config creation (default config written to `%APPDATA%\razer-joro\config.toml`)
- [x] Ctrl+C handler for clean shutdown (releases BLE, runs Drop, exits cleanly)
- [x] Joro matrix index discovery — `scan <batch>` CLI subcommand; ~60 of ~85 keys mapped (2026-04-13)
- [x] Firmware protocol corrected — `set_fn_layer_remap` → `set_layer_remap`; `args[0]=0x01` is constant, not a layer selector (Synapse capture 2026-04-13)
- [x] BLE slot selector architecture discovered — firmware-internal handler bypasses matrix; matrix remaps are safe to use
- [x] **F4 corrected** — F4 is NOT a firmware macro (earlier "Win+Tab macro" wording was wrong); F4 toggles fn/mm behaviour like F5-F12. Verified 2026-04-15.
- [x] Consumer HID interception layer built (`src/consumer_hook.rs`) — hidapi reads confirmed non-consuming on Windows, layer used for discovery/logging. Next step: dispatch to `SpecialAction` table for F8/F9 MM-mode brightness.
- [x] **Synapse mm↔fn primary setting fully decoded** — BLE Protocol30 `SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0]`. Earlier "purely host-side" claim was wrong — it IS a single firmware write. Memory `project_fnmm_toggle_solved.md` supersedes prior guesses.
- [x] **DDC/CI monitor brightness backend** — `src/brightness.rs` using `dxva2.dll` Monitor Configuration API works on the user's Falcon 5120x1440 via VCP 0x10.
- [ ] **Consumer-hook brightness fallback** — route Consumer BrightnessDown/Up from `consumer_hook.rs` into the `SpecialAction` dispatch so F8/F9 brightness remap fires even when firmware stays in MM. Task #10.
- [ ] Icons redraw — current PIL-generated ICO looks pixelated
- [ ] Error handling polish
- [ ] Strip debug `eprintln!` from `ble.rs` + unused rzcontrol F1-F4 constants, remove `fn_detect` diagnostic subcommand
- [ ] Single `.exe` release build via `cargo build --release` (LTO + strip already configured)
