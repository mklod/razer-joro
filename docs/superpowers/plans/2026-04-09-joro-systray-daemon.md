# Joro Systray Daemon Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a minimal Windows systray daemon that applies key remaps and lighting to the Razer Joro keyboard from a TOML config, with auto-reconnect on device replug.

**Architecture:** Single Rust binary with four modules: USB transport (rusb control transfers porting the Python prototype), config loader (serde + toml), remap engine (firmware 1:1 swaps + host-side combos via WH_KEYBOARD_LL), and systray (tray-icon + winit). The main event loop polls for device connect/disconnect and dispatches tray menu events.

**Tech Stack:** Rust, rusb, tray-icon, winit, windows crate (Win32 keyboard hook + SendInput), toml + serde

**Spec:** `docs/superpowers/specs/2026-04-09-joro-systray-daemon-design.md`

**Reference implementation:** `proto/razer_packet.py`, `proto/usb_transport.py`, `proto/commands.py`

---

## File Structure

```
src/
  main.rs       — entry point, event loop, device lifecycle, tray setup
  usb.rs        — RazerPacket builder/parser + RazerDevice (rusb transport)
  config.rs     — TOML schema, loader, key name parser, default config creation
  remap.rs      — host-side keyboard hook (WH_KEYBOARD_LL + SendInput)
  keys.rs       — key name → VK code, key name → HID usage, modifier parsing
  tray.rs       — systray icon + menu builder + state updates
assets/
  icon_connected.ico
  icon_disconnected.ico
Cargo.toml
config.example.toml
```

---

### Task 1: Scaffold Cargo Project

**Files:**
- Create: `Cargo.toml`
- Create: `src/main.rs`
- Create: `src/usb.rs` (empty module)
- Create: `src/config.rs` (empty module)
- Create: `src/remap.rs` (empty module)
- Create: `src/keys.rs` (empty module)
- Create: `src/tray.rs` (empty module)

- [ ] **Step 1: Create Cargo.toml**

```toml
[package]
name = "joro-daemon"
version = "0.1.0"
edition = "2021"

[dependencies]
rusb = "0.9"
tray-icon = "0.19"
winit = { version = "0.30", features = ["rwh_06"] }
windows = { version = "0.58", features = [
    "Win32_UI_WindowsAndMessaging",
    "Win32_UI_Input_KeyboardAndMouse",
    "Win32_Foundation",
    "Win32_System_LibraryLoader",
] }
toml = "0.8"
serde = { version = "1", features = ["derive"] }
image = { version = "0.25", default-features = false, features = ["png"] }

[profile.release]
strip = true
lto = true
```

- [ ] **Step 2: Create src/main.rs with module declarations**

```rust
// src/main.rs
// Last modified: 2026-04-09--2200

mod usb;
mod config;
mod remap;
mod keys;
mod tray;

fn main() {
    println!("joro-daemon starting...");
}
```

- [ ] **Step 3: Create empty module files**

Create each of these with just a comment header:

`src/usb.rs`:
```rust
// src/usb.rs — Razer packet builder + USB device communication
// Last modified: 2026-04-09--2200
```

`src/config.rs`:
```rust
// src/config.rs — TOML config schema and loader
// Last modified: 2026-04-09--2200
```

`src/remap.rs`:
```rust
// src/remap.rs — Host-side keyboard hook remap engine
// Last modified: 2026-04-09--2200
```

`src/keys.rs`:
```rust
// src/keys.rs — Key name / VK code / HID usage lookup tables
// Last modified: 2026-04-09--2200
```

`src/tray.rs`:
```rust
// src/tray.rs — Systray icon and menu
// Last modified: 2026-04-09--2200
```

- [ ] **Step 4: Create config.example.toml**

```toml
# Razer Joro Daemon — Example Config
# Copy to %APPDATA%\razer-joro\config.toml

[lighting]
mode = "static"
color = "#FF4400"
brightness = 200

# Known matrix indices:
#   1=Grave, 2=1, 3=2, 4=3, 5=4, 6=5, 7=6, 8=7, 30=CapsLock

[[remap]]
name = "CapsLock to Ctrl+F12"
from = "CapsLock"
to = "Ctrl+F12"
matrix_index = 30
```

- [ ] **Step 5: Verify it compiles**

Run: `cargo build`
Expected: Compiles with no errors (warnings about unused modules are fine)

- [ ] **Step 6: Commit**

```bash
git add Cargo.toml src/ config.example.toml
git commit -m "feat: scaffold joro-daemon Rust project"
```

---

### Task 2: Key Lookup Tables

**Files:**
- Create: `src/keys.rs`

This is a pure-data module with no external dependencies — easy to test first.

- [ ] **Step 1: Write tests for key name resolution**

Add to `src/keys.rs`:

```rust
// src/keys.rs — Key name / VK code / HID usage lookup tables
// Last modified: 2026-04-09--2200

/// Windows Virtual Key code
pub type VkCode = u16;
/// HID Keyboard Usage ID (from USB HID Usage Tables, page 0x07)
pub type HidUsage = u8;

/// Resolve a key name (e.g., "CapsLock", "F12", "A") to a Windows VK code.
/// Returns None for unknown key names.
pub fn key_name_to_vk(name: &str) -> Option<VkCode> {
    todo!()
}

/// Resolve a key name to an HID keyboard usage ID.
/// Returns None for unknown key names.
pub fn key_name_to_hid(name: &str) -> Option<HidUsage> {
    todo!()
}

/// Parse a target string like "Ctrl+F12" or "Escape" into (modifier_vk_list, key_vk).
/// Modifier prefixes: Ctrl, Shift, Alt, Win
/// Returns None if any part is unrecognized.
pub fn parse_key_combo(combo: &str) -> Option<(Vec<VkCode>, VkCode)> {
    todo!()
}

/// Parse a target string into (modifier_mask, hid_usage) for firmware remaps.
/// modifier_mask: bitmask of HID modifier bits (0x01=LCtrl, 0x02=LShift, 0x04=LAlt, 0x08=LGui)
/// Returns None if any part is unrecognized or if it's a combo (firmware can't do combos).
pub fn parse_single_hid_key(name: &str) -> Option<HidUsage> {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_capslock_vk() {
        assert_eq!(key_name_to_vk("CapsLock"), Some(0x14));
    }

    #[test]
    fn test_f12_vk() {
        assert_eq!(key_name_to_vk("F12"), Some(0x7B));
    }

    #[test]
    fn test_escape_vk() {
        assert_eq!(key_name_to_vk("Escape"), Some(0x1B));
    }

    #[test]
    fn test_letter_a_vk() {
        assert_eq!(key_name_to_vk("A"), Some(0x41));
    }

    #[test]
    fn test_unknown_key_vk() {
        assert_eq!(key_name_to_vk("FooBar"), None);
    }

    #[test]
    fn test_case_insensitive_vk() {
        assert_eq!(key_name_to_vk("capslock"), Some(0x14));
        assert_eq!(key_name_to_vk("CAPSLOCK"), Some(0x14));
    }

    #[test]
    fn test_capslock_hid() {
        assert_eq!(key_name_to_hid("CapsLock"), Some(0x39));
    }

    #[test]
    fn test_escape_hid() {
        assert_eq!(key_name_to_hid("Escape"), Some(0x29));
    }

    #[test]
    fn test_f12_hid() {
        assert_eq!(key_name_to_hid("F12"), Some(0x45));
    }

    #[test]
    fn test_letter_a_hid() {
        assert_eq!(key_name_to_hid("A"), Some(0x04));
    }

    #[test]
    fn test_parse_single_key() {
        let (mods, key) = parse_key_combo("Escape").unwrap();
        assert!(mods.is_empty());
        assert_eq!(key, 0x1B);
    }

    #[test]
    fn test_parse_ctrl_f12() {
        let (mods, key) = parse_key_combo("Ctrl+F12").unwrap();
        assert_eq!(mods, vec![0xA2]); // VK_LCONTROL
        assert_eq!(key, 0x7B);        // VK_F12
    }

    #[test]
    fn test_parse_ctrl_shift_f12() {
        let (mods, key) = parse_key_combo("Ctrl+Shift+F12").unwrap();
        assert_eq!(mods.len(), 2);
        assert!(mods.contains(&0xA2)); // VK_LCONTROL
        assert!(mods.contains(&0xA0)); // VK_LSHIFT
        assert_eq!(key, 0x7B);
    }

    #[test]
    fn test_parse_single_hid() {
        assert_eq!(parse_single_hid_key("Escape"), Some(0x29));
        assert_eq!(parse_single_hid_key("LCtrl"), Some(0xE0));
    }

    #[test]
    fn test_parse_single_hid_rejects_combo() {
        assert_eq!(parse_single_hid_key("Ctrl+F12"), None);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test --lib keys`
Expected: All tests fail with `not yet implemented`

- [ ] **Step 3: Implement the lookup tables and parsers**

Replace the `todo!()` implementations in `src/keys.rs`:

```rust
use std::collections::HashMap;
use std::sync::LazyLock;

pub type VkCode = u16;
pub type HidUsage = u8;

struct KeyInfo {
    vk: VkCode,
    hid: HidUsage,
}

static KEY_TABLE: LazyLock<HashMap<String, KeyInfo>> = LazyLock::new(|| {
    let entries: &[(&str, u16, u8)] = &[
        // Letters
        ("a", 0x41, 0x04), ("b", 0x42, 0x05), ("c", 0x43, 0x06), ("d", 0x44, 0x07),
        ("e", 0x45, 0x08), ("f", 0x46, 0x09), ("g", 0x47, 0x0A), ("h", 0x48, 0x0B),
        ("i", 0x49, 0x0C), ("j", 0x4A, 0x0D), ("k", 0x4B, 0x0E), ("l", 0x4C, 0x0F),
        ("m", 0x4D, 0x10), ("n", 0x4E, 0x11), ("o", 0x4F, 0x12), ("p", 0x50, 0x13),
        ("q", 0x51, 0x14), ("r", 0x52, 0x15), ("s", 0x53, 0x16), ("t", 0x54, 0x17),
        ("u", 0x55, 0x18), ("v", 0x56, 0x19), ("w", 0x57, 0x1A), ("x", 0x58, 0x1B),
        ("y", 0x59, 0x1C), ("z", 0x5A, 0x1D),
        // Digits
        ("0", 0x30, 0x27), ("1", 0x31, 0x1E), ("2", 0x32, 0x1F), ("3", 0x33, 0x20),
        ("4", 0x34, 0x21), ("5", 0x35, 0x22), ("6", 0x36, 0x23), ("7", 0x37, 0x24),
        ("8", 0x38, 0x25), ("9", 0x39, 0x26),
        // F-keys
        ("f1", 0x70, 0x3A), ("f2", 0x71, 0x3B), ("f3", 0x72, 0x3C), ("f4", 0x73, 0x3D),
        ("f5", 0x74, 0x3E), ("f6", 0x75, 0x3F), ("f7", 0x76, 0x40), ("f8", 0x77, 0x41),
        ("f9", 0x78, 0x42), ("f10", 0x79, 0x43), ("f11", 0x7A, 0x44), ("f12", 0x7B, 0x45),
        // Navigation
        ("escape", 0x1B, 0x29), ("enter", 0x0D, 0x28), ("backspace", 0x08, 0x2A),
        ("tab", 0x09, 0x2B), ("space", 0x20, 0x2C), ("capslock", 0x14, 0x39),
        ("insert", 0x2D, 0x49), ("delete", 0x2E, 0x4C), ("home", 0x24, 0x4A),
        ("end", 0x23, 0x4D), ("pageup", 0x21, 0x4B), ("pagedown", 0x22, 0x4E),
        ("up", 0x26, 0x52), ("down", 0x28, 0x51), ("left", 0x25, 0x50), ("right", 0x27, 0x4F),
        ("printscreen", 0x2C, 0x46), ("scrolllock", 0x91, 0x47), ("pause", 0x13, 0x48),
        // Punctuation
        ("grave", 0xC0, 0x35), ("minus", 0xBD, 0x2D), ("equal", 0xBB, 0x2E),
        ("lbracket", 0xDB, 0x2F), ("rbracket", 0xDD, 0x30), ("backslash", 0xDC, 0x31),
        ("semicolon", 0xBA, 0x33), ("quote", 0xDE, 0x34), ("comma", 0xBC, 0x36),
        ("period", 0xBE, 0x37), ("slash", 0xBF, 0x38),
        // Modifiers — VK uses left-specific codes, HID uses 0xE0-0xE7
        ("lctrl", 0xA2, 0xE0), ("lshift", 0xA0, 0xE1), ("lalt", 0xA4, 0xE2), ("lgui", 0x5B, 0xE3),
        ("rctrl", 0xA3, 0xE4), ("rshift", 0xA1, 0xE5), ("ralt", 0xA5, 0xE6), ("rgui", 0x5C, 0xE7),
        // Application key
        ("app", 0x5D, 0x65),
    ];
    let mut map = HashMap::new();
    for &(name, vk, hid) in entries {
        map.insert(name.to_string(), KeyInfo { vk, hid });
    }
    map
});

/// Modifier prefix → VK code for SendInput
static MODIFIER_PREFIX: LazyLock<HashMap<String, VkCode>> = LazyLock::new(|| {
    HashMap::from([
        ("ctrl".to_string(), 0xA2u16),  // VK_LCONTROL
        ("shift".to_string(), 0xA0),     // VK_LSHIFT
        ("alt".to_string(), 0xA4),       // VK_LMENU
        ("win".to_string(), 0x5B),       // VK_LWIN
    ])
});

pub fn key_name_to_vk(name: &str) -> Option<VkCode> {
    KEY_TABLE.get(&name.to_lowercase()).map(|k| k.vk)
}

pub fn key_name_to_hid(name: &str) -> Option<HidUsage> {
    KEY_TABLE.get(&name.to_lowercase()).map(|k| k.hid)
}

pub fn parse_key_combo(combo: &str) -> Option<(Vec<VkCode>, VkCode)> {
    let parts: Vec<&str> = combo.split('+').collect();
    if parts.is_empty() {
        return None;
    }
    let key_name = parts.last().unwrap().trim();
    let key_vk = key_name_to_vk(key_name)?;

    let mut mods = Vec::new();
    for &part in &parts[..parts.len() - 1] {
        let mod_vk = MODIFIER_PREFIX.get(&part.trim().to_lowercase())?;
        mods.push(*mod_vk);
    }
    Some((mods, key_vk))
}

pub fn parse_single_hid_key(name: &str) -> Option<HidUsage> {
    if name.contains('+') {
        return None;
    }
    key_name_to_hid(name)
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib keys`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/keys.rs
git commit -m "feat: key lookup tables — VK codes, HID usages, combo parsing"
```

---

### Task 3: Razer Packet Builder and Parser

**Files:**
- Create: `src/usb.rs`

Port the packet builder/parser from `proto/razer_packet.py`. This is pure logic — fully unit-testable.

- [ ] **Step 1: Write tests for packet building and parsing**

Add to `src/usb.rs`:

```rust
// src/usb.rs — Razer packet builder + USB device communication
// Last modified: 2026-04-09--2200

const PACKET_SIZE: usize = 90;
const TRANSACTION_ID: u8 = 0x1F;

// LED constants
pub const VARSTORE: u8 = 0x01;
pub const BACKLIGHT_LED: u8 = 0x05;

// Status codes
pub const STATUS_NEW: u8 = 0x00;
pub const STATUS_OK: u8 = 0x02;
pub const STATUS_NOT_SUPPORTED: u8 = 0x05;

/// Build a 90-byte Razer HID packet.
pub fn build_packet(command_class: u8, command_id: u8, data_size: u8, args: &[u8]) -> [u8; PACKET_SIZE] {
    todo!()
}

/// Parsed fields from a Razer response packet.
#[derive(Debug)]
pub struct ParsedPacket {
    pub status: u8,
    pub transaction_id: u8,
    pub data_size: u8,
    pub command_class: u8,
    pub command_id: u8,
    pub args: Vec<u8>,
    pub crc_valid: bool,
}

/// Parse a 90-byte Razer response packet.
pub fn parse_packet(buf: &[u8; PACKET_SIZE]) -> ParsedPacket {
    todo!()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_build_packet_size() {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        assert_eq!(pkt.len(), 90);
    }

    #[test]
    fn test_build_packet_header() {
        let pkt = build_packet(0x0F, 0x02, 9, &[0x01, 0x05, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00]);
        assert_eq!(pkt[0], STATUS_NEW);
        assert_eq!(pkt[1], TRANSACTION_ID);
        assert_eq!(pkt[5], 9);
        assert_eq!(pkt[6], 0x0F);
        assert_eq!(pkt[7], 0x02);
    }

    #[test]
    fn test_build_packet_args() {
        let pkt = build_packet(0x0F, 0x02, 3, &[0x01, 0x05, 0xC8]);
        assert_eq!(pkt[8], 0x01);
        assert_eq!(pkt[9], 0x05);
        assert_eq!(pkt[10], 0xC8);
        // Rest of args should be zero
        assert_eq!(pkt[11], 0x00);
    }

    #[test]
    fn test_build_packet_crc() {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        // CRC is XOR of bytes 2..88
        let mut expected_crc: u8 = 0;
        for &b in &pkt[2..88] {
            expected_crc ^= b;
        }
        assert_eq!(pkt[0x58], expected_crc);
    }

    #[test]
    fn test_parse_packet_roundtrip() {
        let pkt = build_packet(0x0F, 0x04, 3, &[0x01, 0x05, 0x80]);
        let parsed = parse_packet(&pkt);
        assert_eq!(parsed.status, STATUS_NEW);
        assert_eq!(parsed.transaction_id, TRANSACTION_ID);
        assert_eq!(parsed.command_class, 0x0F);
        assert_eq!(parsed.command_id, 0x04);
        assert_eq!(parsed.data_size, 3);
        assert_eq!(&parsed.args, &[0x01, 0x05, 0x80]);
        assert!(parsed.crc_valid);
    }

    #[test]
    fn test_parse_packet_detects_bad_crc() {
        let mut pkt = build_packet(0x00, 0x81, 0, &[]);
        pkt[0x58] ^= 0xFF; // corrupt CRC
        let parsed = parse_packet(&pkt);
        assert!(!parsed.crc_valid);
    }

    #[test]
    fn test_get_firmware_packet() {
        // Matches what proto/razer_packet.py produces for get_firmware
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        assert_eq!(pkt[6], 0x00);
        assert_eq!(pkt[7], 0x81);
        assert_eq!(pkt[5], 0);
    }

    #[test]
    fn test_set_static_color_packet() {
        let args = [VARSTORE, BACKLIGHT_LED, 0x01, 0x00, 0x00, 0x01, 0xFF, 0x00, 0x00];
        let pkt = build_packet(0x0F, 0x02, 9, &args);
        assert_eq!(pkt[6], 0x0F);
        assert_eq!(pkt[7], 0x02);
        assert_eq!(pkt[5], 9);
        assert_eq!(pkt[8], VARSTORE);
        assert_eq!(pkt[9], BACKLIGHT_LED);
        assert_eq!(pkt[14], 0xFF); // R
        assert_eq!(pkt[15], 0x00); // G
        assert_eq!(pkt[16], 0x00); // B
    }

    #[test]
    fn test_keymap_entry_packet() {
        // Remap index 30 (CapsLock) to Escape (0x29)
        let mut args = [0u8; 18];
        // 10-byte header (zeros) + 8-byte entry
        args[10] = 30;   // index
        args[11] = 0x02; // type1
        args[12] = 0x02; // type2
        args[13] = 0x00; // pad
        args[14] = 0x29; // usage = Escape
        let pkt = build_packet(0x02, 0x0F, 18, &args);
        assert_eq!(pkt[6], 0x02);
        assert_eq!(pkt[7], 0x0F);
        assert_eq!(pkt[5], 18);
        assert_eq!(pkt[8 + 10], 30);   // index in args
        assert_eq!(pkt[8 + 14], 0x29); // usage in args
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test --lib usb`
Expected: All tests fail with `not yet implemented`

- [ ] **Step 3: Implement build_packet and parse_packet**

Replace the `todo!()` implementations:

```rust
fn crc(buf: &[u8]) -> u8 {
    let mut result: u8 = 0;
    for &b in &buf[2..88] {
        result ^= b;
    }
    result
}

pub fn build_packet(command_class: u8, command_id: u8, data_size: u8, args: &[u8]) -> [u8; PACKET_SIZE] {
    let mut buf = [0u8; PACKET_SIZE];
    buf[0x00] = STATUS_NEW;
    buf[0x01] = TRANSACTION_ID;
    buf[0x05] = data_size;
    buf[0x06] = command_class;
    buf[0x07] = command_id;
    let copy_len = args.len().min(80);
    buf[0x08..0x08 + copy_len].copy_from_slice(&args[..copy_len]);
    buf[0x58] = crc(&buf);
    buf[0x59] = 0x00;
    buf
}

pub fn parse_packet(buf: &[u8; PACKET_SIZE]) -> ParsedPacket {
    let data_size = buf[0x05];
    let args_end = 0x08 + (data_size as usize).min(80);
    ParsedPacket {
        status: buf[0x00],
        transaction_id: buf[0x01],
        data_size,
        command_class: buf[0x06],
        command_id: buf[0x07],
        args: buf[0x08..args_end].to_vec(),
        crc_valid: buf[0x58] == crc(buf),
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cargo test --lib usb`
Expected: All tests pass

- [ ] **Step 5: Commit**

```bash
git add src/usb.rs
git commit -m "feat: Razer packet builder and parser with CRC"
```

---

### Task 4: USB Device Communication

**Files:**
- Modify: `src/usb.rs` (add RazerDevice struct after the packet code)

This adds the `rusb`-based device open/send/receive. Can't be unit-tested — requires hardware.

- [ ] **Step 1: Add RazerDevice struct and methods**

Append to `src/usb.rs`:

```rust
use rusb::{Context, DeviceHandle, UsbContext};
use std::time::Duration;

const RAZER_VID: u16 = 0x1532;
const JORO_PID_WIRED: u16 = 0x02CD;
const JORO_PID_DONGLE: u16 = 0x02CE;
const WINDEX: u16 = 0x03;
const WVALUE: u16 = 0x0300;
const SET_REPORT_BMRT: u8 = 0x21;
const SET_REPORT_BREQ: u8 = 0x09;
const GET_REPORT_BMRT: u8 = 0xA1;
const GET_REPORT_BREQ: u8 = 0x01;
const USB_TIMEOUT: Duration = Duration::from_millis(1000);

pub struct RazerDevice {
    handle: DeviceHandle<Context>,
    pid: u16,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum ConnectionType {
    Wired,
    Dongle,
}

impl RazerDevice {
    /// Scan for and open a Razer Joro (wired or dongle).
    pub fn open() -> Option<Self> {
        let ctx = Context::new().ok()?;
        for pid in [JORO_PID_WIRED, JORO_PID_DONGLE] {
            for device in ctx.devices().ok()?.iter() {
                let desc = device.device_descriptor().ok()?;
                if desc.vendor_id() == RAZER_VID && desc.product_id() == pid {
                    if let Ok(handle) = device.open() {
                        return Some(RazerDevice { handle, pid });
                    }
                }
            }
        }
        None
    }

    pub fn connection_type(&self) -> ConnectionType {
        if self.pid == JORO_PID_DONGLE { ConnectionType::Dongle } else { ConnectionType::Wired }
    }

    /// Send a raw 90-byte packet (SET_REPORT), wait, then read response (GET_REPORT).
    fn send_receive(&self, packet: &[u8; PACKET_SIZE]) -> Result<[u8; PACKET_SIZE], String> {
        self.handle
            .write_control(SET_REPORT_BMRT, SET_REPORT_BREQ, WVALUE, WINDEX, packet, USB_TIMEOUT)
            .map_err(|e| format!("SET_REPORT failed: {e}"))?;
        std::thread::sleep(Duration::from_millis(20));
        let mut resp = [0u8; PACKET_SIZE];
        self.handle
            .read_control(GET_REPORT_BMRT, GET_REPORT_BREQ, WVALUE, WINDEX, &mut resp, USB_TIMEOUT)
            .map_err(|e| format!("GET_REPORT failed: {e}"))?;
        Ok(resp)
    }

    /// Send a packet without reading a response (for keymap SET which has no response).
    fn send_only(&self, packet: &[u8; PACKET_SIZE]) -> Result<(), String> {
        self.handle
            .write_control(SET_REPORT_BMRT, SET_REPORT_BREQ, WVALUE, WINDEX, packet, USB_TIMEOUT)
            .map_err(|e| format!("SET_REPORT failed: {e}"))?;
        std::thread::sleep(Duration::from_millis(20));
        Ok(())
    }

    pub fn get_firmware(&self) -> Result<String, String> {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        let resp = self.send_receive(&pkt)?;
        let parsed = parse_packet(&resp);
        if parsed.status != STATUS_OK {
            return Err(format!("get_firmware: status 0x{:02X}", parsed.status));
        }
        Ok(format!("{}.{}", parsed.args.get(0).unwrap_or(&0), parsed.args.get(1).unwrap_or(&0)))
    }

    pub fn set_static_color(&self, r: u8, g: u8, b: u8) -> Result<(), String> {
        let args = [VARSTORE, BACKLIGHT_LED, 0x01, 0x00, 0x00, 0x01, r, g, b];
        let pkt = build_packet(0x0F, 0x02, 9, &args);
        let resp = self.send_receive(&pkt)?;
        let parsed = parse_packet(&resp);
        if parsed.status != STATUS_OK {
            return Err(format!("set_static_color: status 0x{:02X}", parsed.status));
        }
        Ok(())
    }

    pub fn set_brightness(&self, level: u8) -> Result<(), String> {
        let args = [VARSTORE, BACKLIGHT_LED, level];
        let pkt = build_packet(0x0F, 0x04, 3, &args);
        let resp = self.send_receive(&pkt)?;
        let parsed = parse_packet(&resp);
        if parsed.status != STATUS_OK {
            return Err(format!("set_brightness: status 0x{:02X}", parsed.status));
        }
        Ok(())
    }

    pub fn set_keymap_entry(&self, index: u8, usage: u8) -> Result<(), String> {
        let mut args = [0u8; 18];
        // 10-byte header (zeros) + 8-byte entry
        args[10] = index;
        args[11] = 0x02; // type1
        args[12] = 0x02; // type2
        args[14] = usage; // HID usage
        let pkt = build_packet(0x02, 0x0F, 18, &args);
        // Keymap SET has no GET_REPORT response — send only
        self.send_only(&pkt)
    }

    /// Quick check if the device is still reachable.
    pub fn is_connected(&self) -> bool {
        let pkt = build_packet(0x00, 0x81, 0, &[]);
        self.send_receive(&pkt).is_ok()
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cargo build`
Expected: Compiles successfully

- [ ] **Step 3: Commit**

```bash
git add src/usb.rs
git commit -m "feat: USB device communication — open, send/receive, lighting, keymap"
```

---

### Task 5: Config Module

**Files:**
- Create: `src/config.rs`

- [ ] **Step 1: Write tests for config parsing**

```rust
// src/config.rs — TOML config schema and loader
// Last modified: 2026-04-09--2200

use serde::Deserialize;
use std::path::PathBuf;

#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    pub lighting: LightingConfig,
    #[serde(default)]
    pub remap: Vec<RemapConfig>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct LightingConfig {
    pub mode: String,
    pub color: String,
    pub brightness: u8,
}

#[derive(Debug, Deserialize, Clone)]
pub struct RemapConfig {
    pub name: String,
    pub from: String,
    pub to: String,
    pub matrix_index: Option<u8>,
}

impl Config {
    pub fn load(path: &std::path::Path) -> Result<Self, String> {
        let text = std::fs::read_to_string(path)
            .map_err(|e| format!("Failed to read config: {e}"))?;
        toml::from_str(&text)
            .map_err(|e| format!("Failed to parse config: {e}"))
    }
}

impl LightingConfig {
    /// Parse "#RRGGBB" hex color string into (r, g, b).
    pub fn parse_color(&self) -> Result<(u8, u8, u8), String> {
        let hex = self.color.strip_prefix('#')
            .ok_or_else(|| format!("Color must start with #: {}", self.color))?;
        if hex.len() != 6 {
            return Err(format!("Color must be 6 hex digits: {}", self.color));
        }
        let r = u8::from_str_radix(&hex[0..2], 16).map_err(|e| format!("Bad red: {e}"))?;
        let g = u8::from_str_radix(&hex[2..4], 16).map_err(|e| format!("Bad green: {e}"))?;
        let b = u8::from_str_radix(&hex[4..6], 16).map_err(|e| format!("Bad blue: {e}"))?;
        Ok((r, g, b))
    }
}

/// Default config file path: %APPDATA%\razer-joro\config.toml
pub fn config_path() -> PathBuf {
    let appdata = std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(appdata).join("razer-joro").join("config.toml")
}

/// Create default config file if it doesn't exist. Returns the path.
pub fn ensure_config() -> Result<PathBuf, String> {
    let path = config_path();
    if !path.exists() {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create config dir: {e}"))?;
        }
        std::fs::write(&path, DEFAULT_CONFIG)
            .map_err(|e| format!("Failed to write default config: {e}"))?;
    }
    Ok(path)
}

const DEFAULT_CONFIG: &str = r#"# Razer Joro Daemon Config

[lighting]
mode = "static"
color = "#FFFFFF"
brightness = 128

# Uncomment and edit to add remaps:
# [[remap]]
# name = "CapsLock to Ctrl+F12"
# from = "CapsLock"
# to = "Ctrl+F12"
# matrix_index = 30
"#;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_minimal_config() {
        let toml_str = r#"
[lighting]
mode = "static"
color = "#FF4400"
brightness = 200
"#;
        let config: Config = toml::from_str(toml_str).unwrap();
        assert_eq!(config.lighting.mode, "static");
        assert_eq!(config.lighting.brightness, 200);
        assert!(config.remap.is_empty());
    }

    #[test]
    fn test_parse_config_with_remaps() {
        let toml_str = r#"
[lighting]
mode = "static"
color = "#FF0000"
brightness = 255

[[remap]]
name = "CapsLock to Ctrl+F12"
from = "CapsLock"
to = "Ctrl+F12"
matrix_index = 30

[[remap]]
name = "Escape to Grave"
from = "Escape"
to = "Grave"
matrix_index = 1
"#;
        let config: Config = toml::from_str(toml_str).unwrap();
        assert_eq!(config.remap.len(), 2);
        assert_eq!(config.remap[0].from, "CapsLock");
        assert_eq!(config.remap[0].to, "Ctrl+F12");
        assert_eq!(config.remap[0].matrix_index, Some(30));
        assert_eq!(config.remap[1].matrix_index, Some(1));
    }

    #[test]
    fn test_parse_color_valid() {
        let lc = LightingConfig { mode: "static".into(), color: "#FF8800".into(), brightness: 100 };
        assert_eq!(lc.parse_color().unwrap(), (0xFF, 0x88, 0x00));
    }

    #[test]
    fn test_parse_color_black() {
        let lc = LightingConfig { mode: "static".into(), color: "#000000".into(), brightness: 0 };
        assert_eq!(lc.parse_color().unwrap(), (0, 0, 0));
    }

    #[test]
    fn test_parse_color_missing_hash() {
        let lc = LightingConfig { mode: "static".into(), color: "FF0000".into(), brightness: 0 };
        assert!(lc.parse_color().is_err());
    }

    #[test]
    fn test_default_config_parses() {
        let config: Config = toml::from_str(DEFAULT_CONFIG).unwrap();
        assert_eq!(config.lighting.color, "#FFFFFF");
        assert_eq!(config.lighting.brightness, 128);
    }
}
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `cargo test --lib config`
Expected: All tests pass (the struct definitions + implementations are already in the test file)

- [ ] **Step 3: Commit**

```bash
git add src/config.rs
git commit -m "feat: TOML config loader with color parsing and defaults"
```

---

### Task 6: Host-Side Keyboard Hook (Remap Engine)

**Files:**
- Create: `src/remap.rs`

Uses Win32 `SetWindowsHookEx(WH_KEYBOARD_LL)` + `SendInput`. Not unit-testable — requires running in a Windows message loop.

- [ ] **Step 1: Implement the remap module**

```rust
// src/remap.rs — Host-side keyboard hook remap engine
// Last modified: 2026-04-09--2200

use crate::keys::{self, VkCode};
use std::sync::Mutex;
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYEVENTF_KEYUP,
    KEYEVENTF_EXTENDEDKEY, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, SetWindowsHookExW, UnhookWindowsHookEx, HHOOK, KBDLLHOOKSTRUCT,
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

/// A host-side combo remap: when `from_vk` is pressed, synthesize `modifiers` + `key_vk`.
#[derive(Debug, Clone)]
pub struct ComboRemap {
    pub from_vk: VkCode,
    pub modifier_vks: Vec<VkCode>,
    pub key_vk: VkCode,
}

static HOOK_HANDLE: Mutex<Option<HHOOK>> = Mutex::new(None);
static REMAP_TABLE: Mutex<Vec<ComboRemap>> = Mutex::new(Vec::new());

/// Build the remap table from config entries.
/// Only includes entries that have modifier combos (firmware-only remaps are excluded).
pub fn build_remap_table(remaps: &[crate::config::RemapConfig]) -> Vec<ComboRemap> {
    let mut table = Vec::new();
    for r in remaps {
        // Parse the target — if it has modifiers, it's a host-side combo
        if let Some((mods, key_vk)) = keys::parse_key_combo(&r.to) {
            if mods.is_empty() {
                continue; // single key — handled by firmware
            }
            if let Some(from_vk) = keys::key_name_to_vk(&r.from) {
                table.push(ComboRemap {
                    from_vk,
                    modifier_vks: mods,
                    key_vk,
                });
            }
        }
    }
    table
}

/// Update the active remap table (thread-safe swap).
pub fn update_remap_table(table: Vec<ComboRemap>) {
    *REMAP_TABLE.lock().unwrap() = table;
}

/// Install the low-level keyboard hook. Must be called from a thread with a message pump.
pub fn install_hook() -> Result<(), String> {
    unsafe {
        let hook = SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook_proc), None, 0)
            .map_err(|e| format!("SetWindowsHookEx failed: {e}"))?;
        *HOOK_HANDLE.lock().unwrap() = Some(hook);
    }
    Ok(())
}

/// Remove the keyboard hook.
pub fn remove_hook() {
    let mut handle = HOOK_HANDLE.lock().unwrap();
    if let Some(hook) = handle.take() {
        unsafe { let _ = UnhookWindowsHookEx(hook); }
    }
}

unsafe extern "system" fn hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code >= 0 {
        let kb = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
        let vk = kb.vkCode as VkCode;
        let is_down = wparam.0 == WM_KEYDOWN as usize || wparam.0 == WM_SYSKEYDOWN as usize;
        let is_up = wparam.0 == WM_KEYUP as usize || wparam.0 == WM_SYSKEYUP as usize;

        // Don't process injected events (prevent recursion from our own SendInput)
        let injected = kb.flags.0 & 0x10 != 0; // LLKHF_INJECTED
        if !injected {
            let table = REMAP_TABLE.lock().unwrap();
            if let Some(combo) = table.iter().find(|c| c.from_vk == vk) {
                if is_down {
                    send_combo_down(&combo.modifier_vks, combo.key_vk);
                    return LRESULT(1); // suppress original key
                } else if is_up {
                    send_combo_up(&combo.modifier_vks, combo.key_vk);
                    return LRESULT(1); // suppress original key
                }
            }
        }
    }
    unsafe { CallNextHookEx(None, code, wparam, lparam) }
}

fn send_combo_down(modifiers: &[VkCode], key: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    for &m in modifiers {
        inputs.push(make_key_input(m, false));
    }
    inputs.push(make_key_input(key, false));
    unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32); }
}

fn send_combo_up(modifiers: &[VkCode], key: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    inputs.push(make_key_input(key, true));
    for &m in modifiers.iter().rev() {
        inputs.push(make_key_input(m, true));
    }
    unsafe { SendInput(&inputs, std::mem::size_of::<INPUT>() as i32); }
}

fn make_key_input(vk: VkCode, key_up: bool) -> INPUT {
    let mut flags = KEYEVENTF_EXTENDEDKEY;
    if key_up {
        flags |= KEYEVENTF_KEYUP;
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: 0,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cargo build`
Expected: Compiles (may need to adjust `windows` crate feature flags — add any missing features to `Cargo.toml`)

- [ ] **Step 3: Commit**

```bash
git add src/remap.rs
git commit -m "feat: host-side keyboard hook remap engine (WH_KEYBOARD_LL + SendInput)"
```

---

### Task 7: Systray Icon and Menu

**Files:**
- Create: `src/tray.rs`
- Create: `assets/` directory with placeholder icons

- [ ] **Step 1: Create placeholder icons**

Generate two 32x32 PNG icons programmatically (green circle = connected, grey circle = disconnected). Or create minimal `.ico` files. For now, generate them with the `image` crate at build time, or embed raw RGBA bytes.

Create `src/tray.rs`:

```rust
// src/tray.rs — Systray icon and menu
// Last modified: 2026-04-09--2200

use tray_icon::menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, TrayIcon, TrayIconBuilder};

pub struct JoroTray {
    _tray: TrayIcon,
    pub menu_reload_id: tray_icon::menu::MenuId,
    pub menu_open_config_id: tray_icon::menu::MenuId,
    pub menu_quit_id: tray_icon::menu::MenuId,
    status_item: MenuItem,
    firmware_item: MenuItem,
}

pub fn create_icon(connected: bool) -> Icon {
    // 32x32 RGBA icon — green if connected, grey if not
    let (r, g, b) = if connected { (0x00, 0xCC, 0x44) } else { (0x88, 0x88, 0x88) };
    let mut rgba = Vec::with_capacity(32 * 32 * 4);
    for y in 0..32i32 {
        for x in 0..32i32 {
            let dx = x - 16;
            let dy = y - 16;
            if dx * dx + dy * dy <= 14 * 14 {
                rgba.extend_from_slice(&[r, g, b, 0xFF]);
            } else {
                rgba.extend_from_slice(&[0, 0, 0, 0]);
            }
        }
    }
    Icon::from_rgba(rgba, 32, 32).expect("Failed to create icon")
}

impl JoroTray {
    pub fn new() -> Self {
        let status_item = MenuItem::new("Razer Joro — Disconnected", false, None);
        let firmware_item = MenuItem::new("Firmware: —", false, None);
        let reload_item = MenuItem::new("Reload Config", true, None);
        let open_config_item = MenuItem::new("Open Config File", true, None);
        let quit_item = MenuItem::new("Quit", true, None);

        let menu = Menu::new();
        let _ = menu.append(&status_item);
        let _ = menu.append(&firmware_item);
        let _ = menu.append(&PredefinedMenuItem::separator());
        let _ = menu.append(&reload_item);
        let _ = menu.append(&open_config_item);
        let _ = menu.append(&PredefinedMenuItem::separator());
        let _ = menu.append(&quit_item);

        let icon = create_icon(false);
        let tray = TrayIconBuilder::new()
            .with_tooltip("Joro Daemon")
            .with_icon(icon)
            .with_menu(Box::new(menu))
            .build()
            .expect("Failed to create tray icon");

        JoroTray {
            _tray: tray,
            menu_reload_id: reload_item.id().clone(),
            menu_open_config_id: open_config_item.id().clone(),
            menu_quit_id: quit_item.id().clone(),
            status_item,
            firmware_item,
        }
    }

    pub fn set_connected(&mut self, connected: bool, firmware: Option<&str>) {
        let icon = create_icon(connected);
        self._tray.set_icon(Some(icon)).ok();

        if connected {
            let fw = firmware.unwrap_or("?");
            self.status_item.set_text(format!("Razer Joro — Connected"));
            self.firmware_item.set_text(format!("Firmware: {fw}"));
        } else {
            self.status_item.set_text("Razer Joro — Disconnected");
            self.firmware_item.set_text("Firmware: —");
        }
    }
}

/// Check for menu events (non-blocking).
pub fn poll_menu_event() -> Option<MenuEvent> {
    MenuEvent::receiver().try_recv().ok()
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cargo build`
Expected: Compiles

- [ ] **Step 3: Commit**

```bash
git add src/tray.rs
git commit -m "feat: systray icon and menu with connected/disconnected state"
```

---

### Task 8: Main Event Loop and Device Lifecycle

**Files:**
- Modify: `src/main.rs`

Tie everything together: load config, open device, install hook, run event loop with 2s device poll.

- [ ] **Step 1: Implement main.rs**

```rust
// src/main.rs
// Last modified: 2026-04-09--2200

mod config;
mod keys;
mod remap;
mod tray;
mod usb;

use std::time::{Duration, Instant};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::WindowId;

const POLL_INTERVAL: Duration = Duration::from_secs(2);
const CONFIG_POLL_INTERVAL: Duration = Duration::from_secs(5);

struct App {
    tray: Option<tray::JoroTray>,
    device: Option<usb::RazerDevice>,
    config: config::Config,
    config_path: std::path::PathBuf,
    config_modified: Option<std::time::SystemTime>,
    last_device_poll: Instant,
    last_config_poll: Instant,
}

impl App {
    fn new() -> Self {
        let config_path = config::ensure_config().expect("Failed to create config");
        let cfg = config::Config::load(&config_path).expect("Failed to load config");
        let config_modified = std::fs::metadata(&config_path).ok()
            .and_then(|m| m.modified().ok());

        App {
            tray: None,
            device: None,
            config: cfg,
            config_path,
            config_modified,
            last_device_poll: Instant::now(),
            last_config_poll: Instant::now(),
        }
    }

    fn try_connect(&mut self) {
        if self.device.is_some() {
            return;
        }
        if let Some(dev) = usb::RazerDevice::open() {
            eprintln!("Device connected ({:?})", dev.connection_type());
            self.apply_config(&dev);
            if let Ok(fw) = dev.get_firmware() {
                if let Some(tray) = &mut self.tray {
                    tray.set_connected(true, Some(&fw));
                }
            } else if let Some(tray) = &mut self.tray {
                tray.set_connected(true, None);
            }
            self.device = Some(dev);
        }
    }

    fn apply_config(&self, dev: &usb::RazerDevice) {
        // Apply lighting
        if let Ok((r, g, b)) = self.config.lighting.parse_color() {
            if let Err(e) = dev.set_static_color(r, g, b) {
                eprintln!("Failed to set color: {e}");
            }
        }
        if let Err(e) = dev.set_brightness(self.config.lighting.brightness) {
            eprintln!("Failed to set brightness: {e}");
        }

        // Apply firmware remaps (single-key only)
        for r in &self.config.remap {
            if r.to.contains('+') {
                continue; // combo — handled by host hook
            }
            if let (Some(idx), Some(usage)) = (r.matrix_index, keys::key_name_to_hid(&r.to)) {
                if let Err(e) = dev.set_keymap_entry(idx, usage) {
                    eprintln!("Failed to set keymap entry {}: {e}", r.name);
                }
            }
        }
    }

    fn check_device(&mut self) {
        if let Some(ref dev) = self.device {
            if !dev.is_connected() {
                eprintln!("Device disconnected");
                self.device = None;
                if let Some(tray) = &mut self.tray {
                    tray.set_connected(false, None);
                }
            }
        } else {
            self.try_connect();
        }
    }

    fn reload_config(&mut self) {
        match config::Config::load(&self.config_path) {
            Ok(cfg) => {
                self.config = cfg;
                // Update host-side remap table
                let table = remap::build_remap_table(&self.config.remap);
                remap::update_remap_table(table);
                // Reapply to device if connected
                if let Some(ref dev) = self.device {
                    self.apply_config(dev);
                }
                eprintln!("Config reloaded");
            }
            Err(e) => eprintln!("Config reload failed: {e}"),
        }
    }

    fn check_config_changed(&mut self) {
        let current = std::fs::metadata(&self.config_path).ok()
            .and_then(|m| m.modified().ok());
        if current != self.config_modified {
            self.config_modified = current;
            self.reload_config();
        }
    }

    fn handle_menu_event(&mut self, event_loop: &ActiveEventLoop) {
        if let Some(event) = tray::poll_menu_event() {
            let tray = self.tray.as_ref().unwrap();
            if event.id() == &tray.menu_quit_id {
                remap::remove_hook();
                event_loop.exit();
            } else if event.id() == &tray.menu_reload_id {
                self.reload_config();
            } else if event.id() == &tray.menu_open_config_id {
                let _ = std::process::Command::new("cmd")
                    .args(["/C", "start", "", &self.config_path.to_string_lossy()])
                    .spawn();
            }
        }
    }
}

impl ApplicationHandler for App {
    fn resumed(&mut self, _event_loop: &ActiveEventLoop) {
        if self.tray.is_none() {
            self.tray = Some(tray::JoroTray::new());

            // Install keyboard hook
            if let Err(e) = remap::install_hook() {
                eprintln!("Failed to install keyboard hook: {e}");
            }

            // Build initial remap table
            let table = remap::build_remap_table(&self.config.remap);
            remap::update_remap_table(table);

            // Try initial device connection
            self.try_connect();
        }
    }

    fn window_event(&mut self, _event_loop: &ActiveEventLoop, _id: WindowId, _event: WindowEvent) {}

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        // Poll device connection
        if self.last_device_poll.elapsed() >= POLL_INTERVAL {
            self.last_device_poll = Instant::now();
            self.check_device();
        }

        // Poll config changes
        if self.last_config_poll.elapsed() >= CONFIG_POLL_INTERVAL {
            self.last_config_poll = Instant::now();
            self.check_config_changed();
        }

        // Handle tray menu events
        self.handle_menu_event(event_loop);

        // Keep the event loop ticking at ~100ms for responsiveness
        event_loop.set_control_flow(ControlFlow::WaitUntil(
            Instant::now() + Duration::from_millis(100),
        ));
    }
}

fn main() {
    eprintln!("joro-daemon starting...");
    let event_loop = EventLoop::new().expect("Failed to create event loop");
    let mut app = App::new();
    event_loop.run_app(&mut app).expect("Event loop failed");
}
```

- [ ] **Step 2: Verify it compiles**

Run: `cargo build`
Expected: Compiles. Fix any import issues or `windows` crate feature flags.

- [ ] **Step 3: Commit**

```bash
git add src/main.rs
git commit -m "feat: main event loop with device lifecycle, config reload, and tray integration"
```

---

### Task 9: Integration Test on Hardware

**Files:** None new — manual testing

- [ ] **Step 1: Kill Razer services and run**

```bash
taskkill /F /IM razer_elevation_service.exe 2>/dev/null
cargo run
```

Expected: Systray icon appears (grey = disconnected or green = connected). Console shows connection status.

- [ ] **Step 2: Verify firmware query**

With keyboard plugged in via USB, daemon should print firmware version and show green tray icon.

- [ ] **Step 3: Verify lighting**

Edit `%APPDATA%\razer-joro\config.toml`, set `color = "#FF0000"` and `brightness = 200`. Save. Within 5 seconds the daemon should reload and keyboard should turn red.

- [ ] **Step 4: Verify host-side combo remap**

Add to config:
```toml
[[remap]]
name = "CapsLock to Ctrl+F12"
from = "CapsLock"
to = "Ctrl+F12"
matrix_index = 30
```

Save config. Press CapsLock. Verify it sends Ctrl+F12 (open a key tester to confirm).

- [ ] **Step 5: Verify reconnect**

Unplug USB cable. Tray icon should go grey. Plug back in. Tray icon should go green and lighting/remaps should be reapplied automatically.

- [ ] **Step 6: Verify tray menu**

Right-click tray icon. Verify menu items: status, firmware, reload, open config, quit. Test "Open Config File" and "Quit".

- [ ] **Step 7: Commit any fixes**

```bash
git add -A
git commit -m "fix: integration test fixes from hardware validation"
```

---

## Summary

| Task | Description | Testable |
|------|-------------|----------|
| 1 | Scaffold Cargo project | Build check |
| 2 | Key lookup tables | Unit tests |
| 3 | Razer packet builder/parser | Unit tests |
| 4 | USB device communication | Hardware only |
| 5 | TOML config loader | Unit tests |
| 6 | Host-side keyboard hook | Hardware only |
| 7 | Systray icon and menu | Manual |
| 8 | Main event loop + lifecycle | Manual |
| 9 | Integration test on hardware | Manual |
