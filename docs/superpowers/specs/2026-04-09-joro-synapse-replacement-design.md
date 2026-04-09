# Razer Joro — Synapse Replacement Design Spec

## Overview

A lightweight Windows background process to replace Razer Synapse for the Razer Joro keyboard (VID/PID `1532:02CD`). Single-binary Rust application with embedded webview UI, providing backlight control, key remapping, and BLE sleep fix across USB, BLE, and 2.4GHz dongle transports.

## Requirements

### Must Have
- Static single-color backlight with brightness control (0-255)
- Key remapping: 1:1 swaps and simple modifier combos (e.g., `Ctrl+F12`)
- BLE sleep/reconnect delay fix
- Systray icon with settings webview
- Config persistence across restarts (TOML file)
- Auto-detection and switching between USB, BLE, and 2.4GHz transports
- Windows autostart support

### Won't Have (explicitly out of scope)
- Lighting effects (wave, spectrum, breath, reactive, etc.)
- Per-key custom colors / matrix effects
- Macro recording or playback
- Multiple profiles / layers
- Linux or macOS support
- Firmware update functionality

## Architecture

### Approach: Monolith with Embedded WebView

Single Rust process with `tao` + `wry` for a small HTML/CSS/JS settings window. Systray icon opens the webview. All HID/BLE logic runs on separate threads within the same process.

```
┌─────────────────────────────────────────────┐
│              joro.exe (single process)       │
│                                              │
│  ┌──────────┐  ┌───────────┐  ┌──────────┐  │
│  │ HID      │  │ BLE       │  │ Systray  │  │
│  │ Thread   │  │ Thread    │  │ + WebView│  │
│  │ (hidapi) │  │ (btleplug)│  │ (tao+wry)│  │
│  └────┬─────┘  └─────┬─────┘  └────┬─────┘  │
│       │               │             │        │
│       └───────┬───────┘             │        │
│               ▼                     │        │
│       ┌──────────────┐              │        │
│       │ Device Layer │◄─────────────┘        │
│       │ (transport   │  commands from UI     │
│       │  abstraction)│──────────────┐        │
│       └──────────────┘              ▼        │
│                              ┌────────────┐  │
│                              │ Config     │  │
│                              │ (TOML file)│  │
│                              └────────────┘  │
└─────────────────────────────────────────────┘
```

### Why This Approach
- Single process, no IPC complexity
- Rich UI via HTML/CSS with minimal overhead (~5MB binary, uses system WebView2)
- Rust backend gives sub-millisecond HID latency and tiny resource footprint
- UI can be iterated independently from protocol logic (just HTML/CSS/JS changes)

## Transport & Protocol Layer

### Razer HID Packet (90 bytes)

```
[0x00] report_id       = 0x00
[0x01] status           = 0x00 (new command)
[0x02] transaction_id   = 0x1F (Joro-specific)
[0x03-04] data_size     = varies
[0x05] command_class    = varies
[0x06] command_id       = varies
[0x07-87] arguments     = varies
[0x88] CRC              = XOR of bytes 2–87
[0x89] reserved         = 0x00
```

### Transport Trait

```rust
trait RazerTransport {
    fn send(&self, packet: &RazerPacket) -> Result<()>;
    fn receive(&self) -> Result<RazerPacket>;
    fn is_connected(&self) -> bool;
}
```

Three implementations:
- **UsbTransport** — `hidapi-rs`, device `1532:02CD`, feature reports
- **DongleTransport** — `hidapi-rs`, different PID (TBD from enumeration), same packet format (to verify)
- **BleTransport** — `btleplug`, writes to appropriate GATT characteristic, same packet format (to verify)

### Device Manager
- Probes transports in priority order: USB > 2.4GHz > BLE
- Monitors for connect/disconnect (USB hotplug via hidapi polling, BLE via btleplug notifications)
- On transport change, re-applies current config automatically
- Exposes command interface: `set_color()`, `set_brightness()`, `set_remap()`, `set_sleep_config()`

### Known Commands (from openrazer PR #2683)

| Function | class | id | Args |
|---|---|---|---|
| Set brightness | 0x0F | 0x04 | brightness (0-255) |
| Set static color | 0x0F | 0x02 | R, G, B |
| Get brightness | 0x0F | 0x84 | (response in args) |

Key remapping and BLE sleep config commands are unknown — to be captured via USB sniffing in Phase 1.

## Configuration

### File: `%APPDATA%\joro\joro.toml`

```toml
[lighting]
color = "#FF6600"
brightness = 200  # 0-255

[sleep]
# Fields TBD after RE phase
# e.g. timeout_seconds, keepalive_interval

[remaps]
# 1:1 remap
CapsLock = "LeftCtrl"
# Modifier combo
ScrollLock = "Ctrl+F12"
Pause = "Alt+F4"
```

### Remap Rules
- Left side: always a single physical key
- Right side: single key name or modifier combo (`Mod+Key`, `Mod1+Mod2+Key`)
- Key names follow USB HID usage table (human-readable aliases)
- Remaps sent as HID commands to keyboard firmware (not OS-level interception)
- Fallback: if firmware doesn't support combo remaps natively (determined during Phase 1 sniffing), implement host-side interception via Windows low-level keyboard hook as part of Phase 2

### Config Lifecycle
- Loaded at startup, applied immediately after device connection
- UI edits write to TOML, then signal device layer to re-apply
- No in-memory-only state — crash-safe, full restore from file
- File watcher or UI signal triggers re-apply on external edits

## UI & Systray

### Systray Icon
- Colored circle/square reflecting current backlight color
- Right-click menu: `Settings`, `Reconnect`, `---`, `Quit`
- Left-click opens settings webview

### Settings Webview (~400x500px)

```
┌──────────────────────────┐
│  Joro Settings           │
├──────────────────────────┤
│  Lighting                │
│  [Color picker]          │
│  Brightness  [====●====] │
├──────────────────────────┤
│  Key Remaps              │
│  CapsLock    → LeftCtrl  │
│  ScrollLock  → Ctrl+F12  │
│  [+ Add Remap]           │
├──────────────────────────┤
│  Connection              │
│  Status: BLE Connected   │
│  Transport: [auto-badge] │
├──────────────────────────┤
│        [Apply] [Save]    │
└──────────────────────────┘
```

### UI ↔ Backend Communication
- `wry` bidirectional JS ↔ Rust messaging via `invoke()` / `evaluate()`
- JS calls Rust for commands (set color, add remap, etc.)
- Rust pushes state updates to JS (connection status, current config)
- No HTTP server or sockets — in-process function calls
- Color/brightness changes apply live as preview; "Save" persists to TOML
- Window closes to tray, does not exit app

## BLE Sleep Fix

### Root Cause
BLE supervision timeout / connection interval defaults cause multi-second reconnect delay after idle. Synapse likely sends a power/sleep configuration command at startup.

### Approach
1. USB sniff Synapse startup sequence to identify sleep/power config packet
2. Document command_class / command_id / args
3. Send captured command immediately after BLE connection, and re-send after any reconnect
4. If the config is a BLE connection parameter update (not a Razer HID command), handle via `btleplug` connection parameter request API instead

## Startup Sequence

```
1. Load joro.toml from %APPDATA%\joro\
   └─ If missing, create with defaults (white, brightness 255, no remaps)
2. Probe transports (USB → 2.4GHz → BLE)
   └─ First connected transport wins
3. Apply config to device:
   a. Set static color + brightness
   b. Apply key remaps
   c. If BLE: send sleep config
4. Start systray icon (colored to match current backlight)
5. Start transport monitor (hotplug/reconnect loop)
   └─ On new connection: re-apply full config
   └─ On disconnect: update systray status
6. Idle — wait for UI interaction or transport events
```

### Autostart
- Registry key `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` → `joro.exe --minimized`
- `--minimized` flag skips opening settings window, goes straight to tray
- First-run or installer offers to enable autostart

## Development Phases

### Phase 1: Python Prototype (RE & Validation)
- Sniff USB traffic from Synapse (Wireshark + USBPcap)
- Build Python scripts to replay captured packets via `hidapi`
- Validate: static color, brightness, remap commands, sleep config
- Enumerate 2.4GHz dongle PID, test same packet format
- BLE GATT exploration with `bleak`
- **Exit criteria:** all needed commands documented with known class/id/args, working Python scripts for each

### Phase 2: Rust Core (Transport + Config)
- Implement `RazerPacket` builder with CRC
- Implement `UsbTransport` via `hidapi-rs`
- Config loader (TOML)
- CLI test harness: `joro-cli set-color FF6600`, `joro-cli set-brightness 200`, etc.
- **Exit criteria:** all commands work from CLI on USB, config loads and applies correctly

### Phase 3: BLE + Dongle Transports
- `BleTransport` via `btleplug`
- `DongleTransport` via `hidapi-rs` (different PID)
- Transport auto-detection and switching
- BLE sleep fix applied on connect
- **Exit criteria:** wireless operation works, sleep fix verified

### Phase 4: Systray + WebView UI
- Systray via `tao`, webview via `wry`
- HTML/CSS/JS settings panel
- Live color/brightness preview
- Remap editor, connection status display
- **Exit criteria:** full GUI workflow works end-to-end

### Phase 5: Polish & Packaging
- Autostart registration
- First-run config creation
- Error handling (device not found, transport lost mid-command)
- Single `.exe` build (Cargo release, embedded assets)
- **Exit criteria:** clean install-and-run experience, no dependencies

## Testing Strategy

- **Phase 1:** Manual verification against hardware — send command, observe keyboard response
- **Phase 2-3:** Integration tests with mock transport (replay captured packets, verify byte-level correctness). Real hardware tests run manually.
- **Phase 4:** Manual UI testing
- Focus test effort on packet construction correctness (CRC, byte layout). The keyboard itself is the test oracle.

## Tech Stack

| Component | Technology |
|---|---|
| Language (prototype) | Python 3 |
| Language (production) | Rust |
| USB HID (proto) | pyusb + libusb (control transfers, NOT hidapi) |
| USB HID (prod) | rusb (control transfers, NOT hidapi-rs) |
| BLE (proto) | bleak |
| BLE (prod) | btleplug |
| Systray | tao |
| WebView UI | wry |
| Config format | TOML |
| USB capture | Not needed (brute-force scan via pyusb) |
| Reference | openrazer PR #2683 |

## Reference Links

- openrazer PR #2683: https://github.com/openrazer/openrazer/pull/2683
- PR branch: https://github.com/madbrainz/openrazer/tree/add-razer-joro-support
- openrazer main repo: https://github.com/openrazer/openrazer
- Razer Joro VID/PID: `1532:02CD`
