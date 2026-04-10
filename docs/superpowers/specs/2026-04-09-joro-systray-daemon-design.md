# Razer Joro Systray Daemon — Design Spec

**Date:** 2026-04-09
**Status:** Draft

## Overview

A minimal Windows systray daemon that replaces Razer Synapse for the Joro keyboard. Runs on startup, applies key remaps and lighting from a TOML config, and auto-reapplies on device reconnect. Supports both firmware-level 1:1 key swaps and host-side modifier combos via a low-level keyboard hook.

## Goals

- Apply key remaps (firmware + host-side combos) and lighting config on device connect
- Auto-detect connect/disconnect, reapply on reconnect (USB replug resets firmware remaps)
- Systray icon with status indicator and right-click menu
- Single `.exe`, no installer, no driver, no admin required (except USB device access)
- Support both wired (PID 0x02CD) and 2.4GHz dongle (PID 0x02CE) — same protocol

## Non-Goals (for this build)

- WebView settings UI (future stage)
- BLE direct connection (parked — needs auth RE)
- Macro recording/playback
- Per-key RGB / effects beyond static color
- Profile switching

---

## Architecture

Single-process, three modules:

```
┌─────────────────────────────────────┐
│            main / event loop        │
│  (winit event loop + tray-icon)     │
├──────────┬──────────┬───────────────┤
│  usb.rs  │ remap.rs │  config.rs    │
│  device  │  remap   │  TOML parse   │
│  comms   │  engine  │  + reload     │
└──────────┴──────────┴───────────────┘
```

### Module: `usb` — Device Communication

Handles all Razer USB protocol communication via `rusb`.

**Responsibilities:**
- Build/parse 90-byte Razer packets (status, txn_id=0x1F, class, cmd, args, CRC)
- Send SET_REPORT (0x21/0x09) and GET_REPORT (0xA1/0x01) control transfers
- wValue=0x0300, wIndex=0x03 for all transfers
- Device detection: scan for VID 0x1532, PID 0x02CD (wired) or 0x02CE (dongle)
- Connection polling (2s interval)
- Apply firmware remaps: class=0x02, cmd=0x0F, entry format `[idx, 0x02, 0x02, 0x00, usage, 0x00, 0x00, 0x00]`
- Apply lighting: class=0x0F, cmd=0x02 (static color), cmd=0x04 (brightness)

**Public API:**
```rust
struct RazerDevice { /* rusb handle */ }

impl RazerDevice {
    fn open() -> Option<Self>;          // find and claim device
    fn is_connected(&self) -> bool;
    fn set_static_color(&self, r: u8, g: u8, b: u8) -> Result<()>;
    fn set_brightness(&self, level: u8) -> Result<()>;
    fn set_keymap_entry(&self, index: u8, usage: u8) -> Result<()>;
    fn get_firmware(&self) -> Result<String>;
}
```

### Module: `remap` — Key Remap Engine

Two remap paths, determined by config:

**Firmware remaps** (1:1 key swap):
- Sent to device via `set_keymap_entry()` on connect
- Supports any single HID usage, including modifiers alone (e.g., CapsLock -> LCtrl)
- Limited to what firmware supports: one source key -> one target key

**Host-side combos** (modifier + key):
- Uses `SetWindowsHookEx(WH_KEYBOARD_LL)` to intercept keystrokes globally
- When the source key is pressed: suppress it, then synthesize the target combo via `SendInput`
- When the source key is released: release the synthesized keys
- Hook runs on the main thread's message pump (required by Windows)

**Key identification:**
- Source keys are identified by virtual key code (VK) in the hook, not by matrix index
- This means host-side combos work regardless of which physical key is the source
- Firmware remaps use matrix indices (looked up from a mapping table in config)

**Remap config entry:**
```rust
struct RemapEntry {
    from_vk: u16,              // Windows virtual key code
    from_matrix_idx: Option<u8>, // for firmware remaps
    to: RemapTarget,
}

enum RemapTarget {
    SingleKey(u8),             // HID usage -> firmware remap
    Combo { modifiers: u8, key: u8 }, // host-side combo
}
```

**Decision logic:** If `to` is a single key with no modifiers, use firmware remap. If `to` includes modifiers, use host-side combo.

### Module: `config` — TOML Configuration

Config file location: `%APPDATA%\razer-joro\config.toml`

If missing on first run, create a default config with no remaps and white lighting at 50% brightness.

**Schema:**
```toml
# Razer Joro Daemon Config

[lighting]
mode = "static"        # only "static" supported for now
color = "#FF4400"      # hex RGB
brightness = 200       # 0-255

# Known matrix indices:
#   1=Grave, 2=1, 3=2, 4=3, 5=4, 6=5, 7=6, 8=7, 30=CapsLock
# More will be mapped over time.

[[remap]]
name = "CapsLock to Ctrl+F12"
from = "CapsLock"      # resolved to VK_CAPITAL (0x14)
to = "Ctrl+F12"        # host-side combo (has modifier)
matrix_index = 30      # optional, for firmware remaps

[[remap]]
name = "CopilotKey to Ctrl+F12"
from = "CopilotKey"    # VK code TBD
to = "Ctrl+F12"
# matrix_index = TBD
```

**Key names** are resolved to Windows VK codes via a lookup table. Supported modifier prefixes: `Ctrl+`, `Shift+`, `Alt+`, `Win+` (combinable: `Ctrl+Shift+F12`).

**Config reload:** Watch config file for changes (poll every 5s or use `notify` crate). On change: re-read config, reapply firmware remaps, update hook table. Show tray notification on reload.

### Systray

Uses `tray-icon` crate with `winit` event loop.

**Icon states:**
- Green dot or similar: device connected, remaps active
- Grey/red: device disconnected
- Uses embedded PNG icons (compiled into binary)

**Right-click menu:**
- `Razer Joro — Connected` (or `Disconnected`) — status, disabled item
- `Firmware: x.y` — info, disabled item
- `Reload Config` — re-read TOML, reapply
- `Open Config File` — opens TOML in default editor
- `Quit`

### Device Lifecycle

```
Startup
  ├─ Load config.toml (or create default)
  ├─ Try to open device (wired or dongle)
  │   ├─ Found: apply lighting + firmware remaps, install hook
  │   └─ Not found: show disconnected icon, keep polling
  └─ Enter event loop (tray + poll timer)

Poll (every 2s)
  ├─ Device was connected, still connected: no-op
  ├─ Device was connected, now gone: remove firmware state, show disconnected
  ├─ Device was disconnected, now found: apply lighting + firmware remaps, show connected
  └─ Device was disconnected, still gone: no-op

Note: host-side keyboard hook stays installed regardless of device state.
Combos work even when device is disconnected (they operate at OS level).

Config reload
  ├─ Re-read TOML
  ├─ If device connected: reapply firmware remaps + lighting
  ├─ Update hook remap table (swap atomically)
  └─ Show tray notification "Config reloaded"

Quit
  ├─ Remove keyboard hook
  ├─ Close device handle
  └─ Exit
```

---

## Crate Dependencies

| Crate | Purpose |
|-------|---------|
| `rusb` | USB control transfers to Razer device |
| `tray-icon` | Systray icon and menu |
| `winit` | Event loop for tray-icon |
| `windows` | Win32 API: `SetWindowsHookEx`, `SendInput`, `CallNextHookEx` |
| `toml` | Config parsing |
| `serde` + `serde_derive` | Config deserialization |
| `hex_color` or manual | Parse "#RRGGBB" color strings |

## File Structure

```
src/
  main.rs          — entry point, event loop, tray setup
  usb.rs           — Razer packet builder, device communication
  remap.rs         — firmware + host-side remap engine
  config.rs        — TOML schema, loading, key name resolution
  tray.rs          — systray icon, menu, state updates
  keys.rs          — VK code / HID usage / key name lookup tables
assets/
  icon_connected.png
  icon_disconnected.png
Cargo.toml
config.example.toml
```

## Known Matrix Index Table

| Index | Key |
|-------|-----|
| 1 | Grave (`) |
| 2 | 1 |
| 3 | 2 |
| 4 | 3 |
| 5 | 4 |
| 6 | 5 |
| 7 | 6 |
| 8 | 7 |
| 30 | CapsLock |

More indices will be mapped incrementally using `proto/map_all_keys.py`.

## Testing Strategy

- **USB comms:** Validate against hardware — send get_firmware, verify response matches Python prototype
- **Firmware remaps:** Remap CapsLock (idx 30) to Escape, verify on hardware
- **Host-side combos:** Remap CapsLock to Ctrl+F12, verify via a hotkey listener or key event viewer
- **Reconnect:** Unplug/replug USB, verify remaps reapplied automatically
- **Config reload:** Edit TOML while running, verify changes take effect

## Open Questions

- **Copilot key matrix index:** Needs to be mapped using `proto/map_all_keys.py`
- **Dongle PID:** 0x02CE seen in PnP but not yet tested with Razer packets
- **Icon design:** Placeholder PNGs for now, polish later
