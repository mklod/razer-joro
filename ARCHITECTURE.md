# Razer Joro — Project Architecture

_Authoritative overview of the daemon + UI + key-remap stack as of 2026-04-15--0453._

The Razer Joro is a 75%-layout wireless keyboard (VID/PID `1532:02CE` over USB, Bluetooth LE under the name `Joro`). This project is a Rust daemon that replaces Razer Synapse entirely for this keyboard. It ships lighting control, per-key remapping, Fn-layer Hypershift, screen brightness, and keyboard backlight — with a webview settings UI and a system tray icon.

## Goals

- **No Synapse required**, ever, for normal operation. The daemon is the authoritative controller.
- **Per-key programmability for F4-F12** from the webview — bind any F-key to any action.
- **All transports**: wired USB and Bluetooth LE both work. Dongle PID `02CE` not yet tested.
- **Features we care about**: lighting, key remapping, Hypershift, fn/mm toggle, brightness, backlight, settings persistence.
- **Features we don't**: gaming keyswitch optimisation, scroll wheel, macros.

## High-level component map

```
                          ┌────────────────────────────────┐
                          │   assets/settings.html         │
                          │   (WebView2 via wry)           │
                          │                                │
                          │   - 75% keyboard SVG layout    │
                          │   - Click-to-remap popover     │
                          │   - Lighting / brightness UI   │
                          │   - Firmware-mode-aware labels │
                          └──────────────┬─────────────────┘
                                         │ window.ipc.postMessage JSON
                                         │ + window.joroSetState(state)
                                         ▼
┌───────────────────────────────────────────────────────────────────────┐
│                              src/main.rs                              │
│                                                                       │
│  ┌───────────────────┐  ┌──────────────────┐  ┌────────────────────┐  │
│  │  winit event loop │  │   tray (icon +   │  │  settings_window   │  │
│  │  + ApplicationHdlr│  │   right-click    │  │  (wry WebView2)    │  │
│  │                   │  │    submenus)     │  │                    │  │
│  └─────────┬─────────┘  └────────┬─────────┘  └─────────┬──────────┘  │
│            │                     │                      │             │
│            │ UserEvent (cross-thread dispatch)           │             │
│            │ ┌───────────────────────────────────────┐   │             │
│            │ │ SettingsIpc(String)                   │───┘             │
│            │ │ BacklightSet(u8)                      │                 │
│            │ │ CtrlC                                 │                 │
│            │ └───────────────────────────────────────┘                 │
│            ▼                                                           │
│   ┌──────────────────────────────────────────────────────────┐         │
│   │                    App state                            │         │
│   │   config, Box<dyn JoroDevice>, consumer_hook,            │         │
│   │   firmware_fn_primary, cached_battery, ...               │         │
│   └───────┬──────────────────────────────────────────┬───────┘         │
│           │                                          │                 │
└───────────┼──────────────────────────────────────────┼─────────────────┘
            │                                          │
            ▼                                          ▼
  ┌───────────────────┐                    ┌─────────────────────────┐
  │  JoroDevice trait │                    │   remap.rs LL hook      │
  │  (src/device.rs)  │                    │ (WH_KEYBOARD_LL +       │
  │                   │                    │  SendInput)             │
  │  ┌─────┐ ┌─────┐  │                    │                         │
  │  │ USB │ │ BLE │  │                    │  Tables:                │
  │  └──┬──┘ └──┬──┘  │                    │   - REMAP_TABLE         │
  │     │       │     │                    │   - TRIGGER_TABLE       │
  │  src/usb.rs │     │                    │   - FN_HOST_REMAP_TABLE │
  │             │     │                    │   - SPECIAL_ACTION_     │
  │     src/ble.rs    │                    │       TABLE (new)       │
  └───────────────────┘                    └────────┬────────────────┘
                                                    │
                                  ┌─────────────────┼──────────────────┐
                                  │                 │                  │
                                  ▼                 ▼                  ▼
                           ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐
                           │ SendInput   │  │ brightness.rs│  │ post_user_event │
                           │ (keys,      │  │ (DDC/CI      │  │ BacklightSet →  │
                           │ combos,     │  │  via dxva2)  │  │  main → BLE     │
                           │ media VKs)  │  │              │  │  set_brightness │
                           └─────────────┘  └──────────────┘  └─────────────────┘
```

## Data flow: a user presses F5 (with `VolumeMute → F5` remap configured, MM mode)

1. Joro firmware is in MM mode (because `Win+L → Delete` trigger exists in config, auto-detected at connect).
2. Physical F5 key emits a HID Consumer Control usage `0x00E2` (Mute) over the BLE HID GATT service.
3. Windows HID stack converts to `VK_VOLUME_MUTE` (0xAD) and delivers to WH_KEYBOARD_LL.
4. `remap::hook_proc` receives the event, checks `SPECIAL_ACTION_TABLE` (no match for 0xAD), then falls through to `REMAP_TABLE`.
5. Finds the `VolumeMute → F5` entry, calls `send_combo_down([], VK_F5)` then `send_combo_up([], VK_F5)` via SendInput, tagged with `OUR_INJECTION_TAG` so the hook skips its own re-entry.
6. Returns `LRESULT(1)` to suppress the original VK_VOLUME_MUTE.
7. Windows delivers VK_F5 to the foreground app — browser refreshes.

## Data flow: a user clicks "F8" in the webview and saves a remap

1. WebView click handler opens the popover.
2. Popover prefills `From` with `effectiveEmitsOf(k)` which is `BrightnessDown` because `firmwareFnPrimary === false`.
3. User types `Brightness+-25` in `To`, clicks Save.
4. `window.ipc.postMessage(JSON.stringify({action: 'save_remaps', remaps: [...]}))`.
5. `main.rs::handle_settings_ipc` deserialises, merges into `self.config.remap`, calls `config::save_config()`.
6. Calls `remap::build_remap_tables(&self.config.remap)` → returns `(combo, trigger, special)` tuple. The `F8 → Brightness+-25` entry (hypothetically — see known gap below) goes into `special_table` via `parse_special_action`.
7. Calls `remap::update_special_action_table(special_table)` to swap the hook's view atomically.
8. Webview state pushed back via `push_settings_state`.

Known gap: in firmware MM mode, F8 emits Consumer BrightnessDown which never becomes a Win32 VK, so the LL hook never sees it. The daemon's `consumer_hook.rs` (hidapi listener) needs to intercept consumer reports and dispatch to `SPECIAL_ACTION_TABLE` directly — that plumbing is queued as task #10.

## Modules (src/)

### Transport layer

- **`device.rs`** — `JoroDevice` trait. Common methods for USB and BLE: `get_firmware`, `set_brightness`, `set_static_color`, `set_layer_remap`, `set_device_mode`, `get_battery_percent`, `transport_name`.
- **`usb.rs`** — `RazerDevice`: raw USB control transfers via `rusb`. Uses openrazer's 90-byte `razer_report` struct with XOR CRC. Owns keymap programming via `set_layer_remap` (class=0x02 cmd=0x0d, writes the Hypershift slot that wired AND BLE both read from).
- **`ble.rs`** — `BleDevice`: direct WinRT Bluetooth via the `windows` crate. Opens `GattSession` with `MaintainConnection=true` so the connection survives across GATT idles. Enumerates the Razer custom service `52401523-...`, opens TX (`1524`) and RX (`1525`) characteristics, subscribes to notifications.
  - **Protocol30 8-byte header** `[txn, dlen, 0, 0, class, cmd, sub1, sub2]`. SET commands are split writes (header then payload as two separate ATT Write requests). Responses arrive on RX as notifications.
  - **New in 2026-04-15:** `set_device_mode(fn_primary)` / `get_device_mode()` wrapping the decoded `class=0x01 cmd=0x02` mode register. See `memory/project_fnmm_toggle_solved.md`.

### Remap engine

- **`keys.rs`** — Key-name ↔ VK code table, modifier parsing. Handles `Ctrl+F12` and bare names like `VolumeMute`.
- **`remap.rs`** — The brain. Four tables, all checked on every `WH_KEYBOARD_LL` event:
  1. **`TRIGGER_TABLE`** (combo-source): matches `Win+L → Delete`-style remaps where the source is a modifier+key combo the keyboard firmware generates as a macro. Uses a modifier "gate" state machine: when the gate modifier arrives, suppress it and wait for the trigger key to decide the real action. Prefix mods (e.g. LShift before LWin for Copilot) are handled as a known list.
  2. **`FN_HOST_REMAP_TABLE`** (host-side Hypershift): matches on normal keys while `fn_detect::FN_HELD` is true. Reads the Fn state from Joro's vendor HID col05 report byte (`05 04 <state>`). Works on any transport.
  3. **`SPECIAL_ACTION_TABLE`** (new 2026-04-15): non-keyboard actions. Enum `SpecialAction`:
     - `BrightnessDelta(i32)` / `BrightnessAbs(u32)` — dispatch to `brightness.rs` on a spawned thread.
     - `BacklightDelta(i32)` / `BacklightAbs(u8)` — post `UserEvent::BacklightSet(u8)` to the main loop via the `GLOBAL_PROXY` OnceLock.
     - `NoOp` — swallow the key.
  4. **`REMAP_TABLE`** (plain single-key): matches `from_vk` against the current VK, sends `modifier_vks + key_vk` via SendInput.
  - **Injection tagging:** our SendInput calls tag `dwExtraInfo = 0x4A6F524F` ('JoRO'). The hook skips events with this tag to prevent recursion, while still seeing Windows-native media VK injections (dwExtraInfo = 0) so the user can remap VolumeMute → F5.
  - `parse_special_action(&str)` recognises the DSL: `NA`/`NoOp`, `Brightness+Down/Up/±N/=N`, `Backlight+Down/Up/±N/=N`. Plain keys and combos fall through to the existing parser.
  - `build_remap_tables` returns a 3-tuple `(combo, trigger, special)` — call sites: daemon startup, config reload, and the save-from-webview path.

### Non-keyboard action backends

- **`brightness.rs`** (new 2026-04-15) — External-monitor brightness via the Windows Monitor Configuration API (`dxva2.dll`). Enumerates HMONITORs via `EnumDisplayMonitors`, resolves each to a `PHYSICAL_MONITOR` via `GetPhysicalMonitorsFromHMONITOR`, reads min/cur/max via `GetMonitorBrightness`. Supports `vcp_get/vcp_set` for direct VCP feature code writes (e.g. the user's Falcon monitor required testing multiple VCP codes before VCP 0x10 was confirmed working on the 0-50 range it reports). `delta_all(percent)` scales by each monitor's own range. Closes physical monitor handles via `DestroyPhysicalMonitors` on Drop.
- **Joro backlight** — existing `BleDevice::set_brightness(level)` (Protocol30 class=0x10 cmd=0x05 sub=01,00 with single byte 0-255). The `BacklightSet` event handler calls this and persists `config.lighting.brightness` so the new value survives restart.

### Fn detection

- **`fn_detect.rs`** — Spawns a reader thread per HID collection looking for the Razer vendor report pattern `05 04 <state>` on col05. Toggles `FN_HELD: AtomicBool` which `remap.rs` reads on every hook call. Works on any transport because hidapi reads are non-consuming on Windows.

### UI

- **`tray.rs`** — System tray icon, left-click opens settings, right-click submenu for Color / Brightness / Effect presets + Autostart toggle. Icon shows connected (colour) vs disconnected (grey + red LED).
- **`settings_window.rs`** — wry WebView2 wrapper. Serves `assets/settings.html` as an in-memory resource, wires `window.ipc.postMessage` to the winit user-event loop, provides `eval()` for pushing state updates via `window.joroSetState(state)`.
- **`assets/settings.html`** — Single-file HTML/CSS/JS. 75% Joro keyboard drawn with inline SVG key icons. Click a key → popover with From/To text inputs + hint line explaining the current firmware mode and what the key actually emits. Mode-aware label/tooltip logic via `effectiveEmitsOf(k)`, `firmwareFnPrimary` state, and `.key.ble-locked` styling for F1/F2/F3.

### Config

- **`config.rs`** — TOML schema via `serde`. Fields:
  - `lighting: { mode, color, brightness }`
  - `remap: Vec<{ name, from, to, matrix_index }>` — single-key, combo, and DSL special-action targets all live here
  - `fn_remap: Vec<{ name, from, to, matrix_index }>` — firmware-level Hypershift (USB-programmed, persists in firmware)
  - `fn_host_remap: Vec<{ name, from, to }>` — host-side Hypershift (LL hook + fn_detect, works on any transport)
  - `consumer_remap: Vec<ConsumerRemapConfig>` — experimental Consumer HID interception
  - `ble_fn_primary: bool` — legacy rzcontrol filter-driver toggle, superseded by `device_mode`
  - `device_mode: String` — **new 2026-04-15**. `"auto"` (default), `"fn"`, or `"mm"`. Controls the firmware device-mode write at connect time.

### CLI subcommands (for diagnostics)

- `cargo run` — normal daemon run
- `cargo run -- set-mode fn|mm` — manual firmware mode flip
- `cargo run -- brightness info|caps|vcp [CODE [= VAL]]|+N|-N|N` — DDC/CI diagnostics
- `cargo run -- scan <batch>` — Joro matrix index discovery (USB only)
- `cargo run -- fn-detect` — HID report descriptor probing
- `cargo run -- diag-readlayers` — dump USB firmware keymap layers

## Firmware mode auto-detect

```rust
let want_fn = match self.config.device_mode.as_str() {
    "fn" => Some(true),
    "mm" => Some(false),
    _ => {
        // "auto" or unset
        let needs_mm = self.config.remap.iter().any(|r| {
            let lc = r.from.to_ascii_lowercase();
            lc.starts_with("win+") || lc.starts_with("lwin+") || lc.starts_with("rwin+")
        });
        if needs_mm { Some(false) } else { Some(true) }
    }
};
```

The auto heuristic is minimal on purpose: if the config needs a Win-modified trigger remap (Lock = Win+L, Copilot = LShift+LWin+0x86 which shows up as a Win+Copilot entry), we keep firmware in MM so the keyboard emits those combos. Otherwise we default to Fn for full F4-F12 host-side programmability.

Users who want to override get `device_mode = "fn"` or `"mm"` in `%APPDATA%/razer-joro/config.toml`.

## Firmware mode capability matrix

| Key | Fn mode (firmware Fn-primary) | MM mode (firmware MM-primary) |
|---|---|---|
| F1, F2, F3 | **Firmware-locked BLE slot selectors**, bypass HID stack. Can't be remapped host-side on BLE. Hook DOES see them on wired. |
| F4 | Plain `VK_F4` (LL hook can remap) | Plain `VK_F4` (LL hook can remap) |
| F5 | Plain `VK_F5` | Consumer Mute → `VK_VOLUME_MUTE` (LL hook can remap) |
| F6 | Plain `VK_F6` | Consumer VolDn → `VK_VOLUME_DOWN` (LL hook can remap) |
| F7 | Plain `VK_F7` | Consumer VolUp → `VK_VOLUME_UP` (LL hook can remap) |
| F8 | Plain `VK_F8` | Consumer BrightnessDown (NO Win32 VK; needs `consumer_hook.rs`) |
| F9 | Plain `VK_F9` | Consumer BrightnessUp (NO Win32 VK; needs `consumer_hook.rs`) |
| F10 | Plain `VK_F10` | Razer vendor backlight Col06 (not LL-catchable, use native OR force Fn) |
| F11 | Plain `VK_F11` | Razer vendor backlight Col06 (not LL-catchable, use native OR force Fn) |
| F12 | Plain `VK_F12` | `VK_SNAPSHOT` (LL hook can remap) |

## Successful working methods for key remaps

A key remap entry is `[[remap]] name=, from=, to=` in config. The `from` field must match what **Windows actually sees** for that key in the current firmware mode (see the capability matrix). The `to` field accepts:

- **Plain key:** `A`, `F5`, `Home`, `Delete`, `PrintScreen`
- **Combo:** `Ctrl+F12`, `Win+Tab`, `Shift+Home`
- **Media VK:** `VolumeMute`, `VolumeDown`, `VolumeUp`, `MediaPlayPause`, `MediaNextTrack`, `MediaPrevTrack`, `MediaStop`
- **Monitor brightness:** `Brightness+Down`, `Brightness+Up`, `Brightness+15`, `Brightness=50` (via DDC/CI)
- **Joro backlight:** `Backlight+Down`, `Backlight+Up`, `Backlight+32`, `Backlight=200` (via BLE Protocol30 class=0x10)
- **Dead key:** `NA` or `NoOp` — swallows the key

For Hypershift (Fn+key), use `[[fn_host_remap]]` for host-side or `[[fn_remap]]` for firmware-level (USB-programmed).

For combo-source triggers (e.g. a physical "Lock" key that the firmware emits as `Win+L`), use `[[remap]] from = "Win+L"` — the trigger-table state machine will gate the Win modifier and resolve on the next key.

## Known protocol facts

- **BLE Protocol30 header**: `[txn, dlen, 0, 0, class, cmd, sub1, sub2]` (8 bytes). SET commands are split writes.
- **Fn-layer firmware write**: class=0x02 cmd=0x0d with 10-byte args `[0x01, matrix, 0x01, 0x02, 0x02, mod, usage, 0..0]`. USB only. Wired + BLE read the same slot but the runtime table only refreshes on a wired↔BLE transport cycle.
- **Firmware mode toggle**: class=0x01 cmd=0x02 sub=00,00 data=`[mode, 0]` (split write). mode=0x03 Fn, mode=0x00 MM. GET form cmd=0x82.
- **Lighting**: class=0x10 cmd=0x05 sub=01,00 data=`[brightness]` (1 byte). class=0x10 cmd=0x03 for effects with variable-length payload depending on effect type.
- **Battery**: standard BLE Battery Service (0x180F / 0x2A19) returns `[percent]` directly. Fallback is Protocol30 class=0x07 cmd=0x80.

## Known limits + open items

- **F1/F2/F3 on BLE** — firmware-locked, uncircumventable. UI shows them as solid light-grey "locked" keys.
- **F8/F9 in MM mode** — Consumer BrightnessDown/Up have no Win32 VK. The `to = "Brightness+..."` remap is stored in the special table but the hook never sees the source event. Queued fix: `consumer_hook.rs` dispatches to `SPECIAL_ACTION_TABLE` directly.
- **F10/F11 in MM mode** — Razer vendor Col06 reports aren't LL-catchable. User can either leave them as native backlight (current behaviour, works) or set `device_mode = "fn"` to restore programmable F10/F11 at the cost of Lock/Copilot.
- **USB dongle transport (PID 02CE)** — untested, not yet in `JoroDevice`.
- **Release build polish** — strip debug `eprintln!`, remove unused rzcontrol constants, `cargo build --release`.

## Key files

```
Cargo.toml                                — deps + windows crate features
src/main.rs                               — event loop, App state, CLI subcommands
src/config.rs                             — TOML schema
src/device.rs                             — JoroDevice trait
src/usb.rs                                — USB transport (rusb)
src/ble.rs                                — BLE transport (WinRT)
src/remap.rs                              — LL hook + all 4 remap tables + DSL parser
src/brightness.rs                         — monitor DDC/CI backend (NEW 2026-04-15)
src/keys.rs                               — VK table + key-name parser
src/fn_detect.rs                          — Fn-held state via vendor HID Col05
src/consumer_hook.rs                      — hidapi consumer-page listener
src/tray.rs                               — systray icon + menu
src/settings_window.rs                    — wry WebView2 wrapper
assets/settings.html                      — webview UI (single file)
assets/joro_icon.ico                      — tray icon

memory/project_fnmm_toggle_solved.md      — the hot fn/mm decoding write-up
memory/project_razer_filter_driver_ioctls.md — rzcontrol filter IOCTL reference (legacy path)
memory/project_ble_findings.md            — Protocol30 split-write discovery
memory/project_hypershift_commit_trigger.md — wired↔BLE cycle refreshes firmware layer
memory/project_host_side_fn_detection.md  — HID col05 Fn-state trick
memory/project_ble_keymap_is_hostside.md  — BLE Hypershift = host-side LL hook, not firmware write

_status.md       — session-by-session log
CHANGELOG.md     — user-facing build history with testing checklists
WORKPLAN.md      — stage-based TODO
ARCHITECTURE.md  — this file
```
