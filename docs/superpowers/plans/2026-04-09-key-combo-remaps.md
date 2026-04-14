# Key Combo Remaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix Copilot key SendInput output and add Lock key → Delete remap via a unified pending-modifier state machine.

**Architecture:** Replace the hardcoded `COMPANION_MODIFIERS` array and `PendingCompanion` struct with a data-driven `PendingModifierRemap` table built from config. The hook callback uses this table to hold modifier keydowns, wait for trigger keys, and emit output combos or single keys. `make_key_input` gets scan codes via `MapVirtualKeyW` so SendInput reaches all apps.

**Tech Stack:** Rust, windows crate (Win32_UI_Input_KeyboardAndMouse), existing joro-daemon architecture.

**Build command:** `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`

**Build (no test):** `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo build" 2>&1`

---

### Task 1: Fix `make_key_input` — add scan codes via MapVirtualKeyW

**Files:**
- Modify: `src/remap.rs:7-9` (imports), `src/remap.rs:274-294` (make_key_input)

- [ ] **Step 1: Add MapVirtualKeyW import**

In `src/remap.rs`, change the `KeyboardAndMouse` import block (lines 7-10) to:

```rust
use windows::Win32::UI::Input::KeyboardAndMouse::{
    MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MAP_VIRTUAL_KEY_TYPE, VIRTUAL_KEY,
};
```

- [ ] **Step 2: Update `make_key_input` to populate wScan**

Replace the `make_key_input` function (lines 274-294) with:

```rust
fn make_key_input(vk: VkCode, key_up: bool) -> INPUT {
    let scan = unsafe {
        MapVirtualKeyW(vk as u32, MAP_VIRTUAL_KEY_TYPE(0)) as u16 // MAPVK_VK_TO_VSC = 0
    };
    let mut flags = KEYBD_EVENT_FLAGS(0);
    if is_extended_key(vk) {
        flags = KEYEVENTF_EXTENDEDKEY;
    }
    if key_up {
        flags = KEYBD_EVENT_FLAGS(flags.0 | KEYEVENTF_KEYUP.0);
    }
    INPUT {
        r#type: INPUT_KEYBOARD,
        Anonymous: INPUT_0 {
            ki: KEYBDINPUT {
                wVk: VIRTUAL_KEY(vk),
                wScan: scan,
                dwFlags: flags,
                time: 0,
                dwExtraInfo: 0,
            },
        },
    }
}
```

- [ ] **Step 3: Verify build passes**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo build" 2>&1`
Expected: Compiles with no errors (warnings OK).

- [ ] **Step 4: Run tests**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: All 30 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/remap.rs
git commit -m "fix: add scan codes to SendInput via MapVirtualKeyW"
```

---

### Task 2: Refactor remap types — unified PendingModifierRemap

**Files:**
- Modify: `src/remap.rs:17-52` (types and statics)

- [ ] **Step 1: Replace PendingCompanion and COMPANION_MODIFIERS with new types**

Replace lines 17-52 (everything from `// ── Types` through the end of `PendingCompanion`) with:

```rust
// ── Types ────────────────────────────────────────────────────────────────────

/// Single-key → combo remap (e.g., CapsLock → Ctrl+F12).
/// Source is one VK, output is modifier(s) + key.
#[derive(Debug, Clone)]
pub struct ComboRemap {
    pub from_vk: VkCode,
    pub modifier_vks: Vec<VkCode>,
    pub key_vk: VkCode,
}

/// Combo-source remap via pending-modifier state machine.
/// Holds a modifier keydown, waits for a trigger key, then emits output.
/// Handles both: Copilot (LWin+0x86 → Ctrl+F12) and Lock (LWin+L → Delete).
#[derive(Debug, Clone)]
pub struct PendingModifierRemap {
    pub held_vk: VkCode,
    pub trigger_vk: VkCode,
    pub output_mods: Vec<VkCode>,
    pub output_key: VkCode,
}

// ── Global state (hook callback is a C function pointer, must be static) ─────

struct SendHook(HHOOK);
unsafe impl Send for SendHook {}

static HOOK_HANDLE: Mutex<Option<SendHook>> = Mutex::new(None);
static REMAP_TABLE: Mutex<Vec<ComboRemap>> = Mutex::new(Vec::new());
static PENDING_MOD_TABLE: Mutex<Vec<PendingModifierRemap>> = Mutex::new(Vec::new());

/// Active pending-modifier state: we suppressed a modifier keydown and are
/// waiting for the trigger key.
static PENDING_STATE: Mutex<Option<PendingState>> = Mutex::new(None);

struct PendingState {
    held_vk: VkCode,
    trigger_vk: VkCode,
    output_mods: Vec<VkCode>,
    output_key: VkCode,
    timestamp: std::time::Instant,
}
```

- [ ] **Step 2: Add `update_pending_mod_table` function**

After the existing `update_remap_table` function (line 104-106), add:

```rust
/// Replace the active pending-modifier remap table.
pub fn update_pending_mod_table(table: Vec<PendingModifierRemap>) {
    *PENDING_MOD_TABLE.lock().unwrap() = table;
}
```

- [ ] **Step 3: Remove old DEBUG_LOG and set_debug_log (keep them but move after new statics)**

Keep `DEBUG_LOG` and `set_debug_log` as-is, just make sure they're after the new statics block. They should remain at the same location relative to the public API section.

- [ ] **Step 4: Verify build passes**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo build" 2>&1`
Expected: Compile errors about removed `PENDING_COMPANION` and `COMPANION_MODIFIERS` in hook_proc — that's expected, we fix those in Task 3.

---

### Task 3: Rewrite hook_proc to use unified pending-modifier table

**Files:**
- Modify: `src/remap.rs:130-234` (hook_proc function)

- [ ] **Step 1: Replace the hook_proc function body**

Replace the entire `hook_proc` function (from `unsafe extern "system" fn hook_proc` through its closing `}`) with:

```rust
unsafe extern "system" fn hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code < 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let kb = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
    let vk = kb.vkCode as VkCode;

    // Skip injected events (LLKHF_INJECTED = 0x10) to prevent recursion
    if kb.flags.0 & 0x10 != 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let msg = wparam.0 as u32;
    let is_down = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
    let is_up = msg == WM_KEYUP || msg == WM_SYSKEYUP;
    let debug = DEBUG_LOG.load(std::sync::atomic::Ordering::Relaxed);

    if debug && is_down {
        let ext = if kb.flags.0 & 0x01 != 0 { "EXT " } else { "" };
        eprintln!("  KEY {ext}vk=0x{vk:04X} scan=0x{:04X}", kb.scanCode);
    }

    // --- Pending-modifier state machine ---
    // On keydown: check if this VK is a held_vk in the pending-modifier table.
    // If so, suppress it and enter pending state.
    if is_down {
        let table = PENDING_MOD_TABLE.lock().unwrap();
        let found = table.iter().find(|r| r.held_vk == vk).cloned();
        drop(table);
        if let Some(pmr) = found {
            *PENDING_STATE.lock().unwrap() = Some(PendingState {
                held_vk: pmr.held_vk,
                trigger_vk: pmr.trigger_vk,
                output_mods: pmr.output_mods,
                output_key: pmr.output_key,
                timestamp: std::time::Instant::now(),
            });
            if debug {
                eprintln!("    holding 0x{:04X}, expecting 0x{:04X}", pmr.held_vk, pmr.trigger_vk);
            }
            return LRESULT(1); // suppress
        }
    }

    // Check pending state: did the expected trigger arrive?
    let pending = PENDING_STATE.lock().unwrap().take();
    if let Some(p) = pending {
        let elapsed = p.timestamp.elapsed();
        if vk == p.trigger_vk && is_down && elapsed.as_millis() < 100 {
            // Trigger arrived — emit the output combo/key
            if debug {
                eprintln!("    trigger 0x{vk:04X} confirmed, emitting output");
            }
            send_combo_down(&p.output_mods, p.output_key);
            // Store the remap info so we can emit key-up later
            // Re-enter pending state with a "confirmed" marker (trigger_vk = 0 means confirmed)
            *PENDING_STATE.lock().unwrap() = Some(PendingState {
                held_vk: p.held_vk,
                trigger_vk: 0, // 0 = confirmed, waiting for key-ups
                output_mods: p.output_mods,
                output_key: p.output_key,
                timestamp: p.timestamp,
            });
            return LRESULT(1); // suppress trigger keydown
        } else if vk == p.trigger_vk && is_up && p.trigger_vk == 0 {
            // This shouldn't happen here (trigger_vk was already cleared).
            // Fall through.
        } else if elapsed.as_millis() >= 100 {
            // Timeout — replay the held modifier
            if debug {
                eprintln!("    pending timeout, replaying 0x{:04X}", p.held_vk);
            }
            let inputs = [make_key_input(p.held_vk, false)];
            send_inputs(&inputs);
            // Fall through to process current key normally
        } else {
            // Different key arrived before trigger — replay held modifier
            if debug {
                eprintln!("    wrong key 0x{vk:04X}, replaying 0x{:04X}", p.held_vk);
            }
            let inputs = [make_key_input(p.held_vk, false)];
            send_inputs(&inputs);
            // Fall through to process current key normally
        }
    }

    // Check for confirmed pending state (trigger_vk == 0): handle key-ups
    let confirmed = {
        let state = PENDING_STATE.lock().unwrap();
        state.as_ref().and_then(|p| {
            if p.trigger_vk == 0 {
                Some((p.held_vk, p.output_mods.clone(), p.output_key))
            } else {
                None
            }
        })
    };
    if let Some((held_vk, output_mods, output_key)) = confirmed {
        // Suppress key-ups for the held modifier and trigger key
        if is_up && (vk == held_vk || vk == self::get_confirmed_trigger(held_vk)) {
            // When the trigger key goes up, release the output combo
            if vk != held_vk {
                // Trigger key-up: release output
                send_combo_up(&output_mods, output_key);
            }
            // When held modifier goes up, clear the confirmed state
            if vk == held_vk {
                *PENDING_STATE.lock().unwrap() = None;
            }
            return LRESULT(1); // suppress
        }
    }

    // --- Standard single-key remap lookup ---
    let table = REMAP_TABLE.lock().unwrap();
    let found = table.iter().find(|r| r.from_vk == vk).cloned();
    drop(table);

    if let Some(remap) = found {
        if is_down {
            if debug {
                eprintln!("    -> remap: mods={:?} key=0x{:04X}", remap.modifier_vks, remap.key_vk);
            }
            send_combo_down(&remap.modifier_vks, remap.key_vk);
            return LRESULT(1);
        } else if is_up {
            send_combo_up(&remap.modifier_vks, remap.key_vk);
            return LRESULT(1);
        }
    }

    CallNextHookEx(None, code, wparam, lparam)
}
```

- [ ] **Step 2: Add helper to look up trigger VK for a confirmed held_vk**

Add this function right before `send_combo_down`:

```rust
/// Look up the trigger VK associated with a held modifier from the pending-mod table.
fn get_confirmed_trigger(held_vk: VkCode) -> VkCode {
    let table = PENDING_MOD_TABLE.lock().unwrap();
    table.iter()
        .find(|r| r.held_vk == held_vk)
        .map(|r| r.trigger_vk)
        .unwrap_or(0)
}
```

- [ ] **Step 3: Verify build passes**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo build" 2>&1`
Expected: Compiles cleanly (warnings OK — unused `PENDING_COMPANION` etc. should be gone now).

- [ ] **Step 4: Run tests**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: All 30 tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/remap.rs
git commit -m "refactor: unified pending-modifier state machine for combo-source remaps"
```

---

### Task 4: Update `build_remap_table` to produce both tables

**Files:**
- Modify: `src/remap.rs:67-101` (build_remap_table)
- Modify: `src/keys.rs:93-101` (MODIFIER_VK — need to expose for combo-source parsing)

- [ ] **Step 1: Write test for combo-source remap parsing**

Add this test at the bottom of `src/remap.rs` (or in a `#[cfg(test)] mod tests` block — create one if it doesn't exist):

```rust
#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::RemapConfig;

    #[test]
    fn test_build_tables_combo_source() {
        let remaps = vec![
            RemapConfig {
                name: "Lock to Delete".into(),
                from: "Win+L".into(),
                to: "Delete".into(),
                matrix_index: None,
            },
        ];
        let (combos, pending) = build_remap_tables(&remaps);
        assert!(combos.is_empty());
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].held_vk, 0x5B); // LWin
        assert_eq!(pending[0].trigger_vk, 0x4C); // L
        assert_eq!(pending[0].output_mods, Vec::<VkCode>::new());
        assert_eq!(pending[0].output_key, 0x2E); // Delete
    }

    #[test]
    fn test_build_tables_combo_output() {
        let remaps = vec![
            RemapConfig {
                name: "CapsLock to Ctrl+F12".into(),
                from: "CapsLock".into(),
                to: "Ctrl+F12".into(),
                matrix_index: Some(30),
            },
        ];
        let (combos, pending) = build_remap_tables(&remaps);
        assert_eq!(combos.len(), 1);
        assert_eq!(combos[0].from_vk, 0x14); // CapsLock
        assert_eq!(combos[0].modifier_vks, vec![0xA2]); // LCtrl
        assert_eq!(combos[0].key_vk, 0x7B); // F12
        assert!(pending.is_empty());
    }

    #[test]
    fn test_build_tables_copilot_combo_source() {
        let remaps = vec![
            RemapConfig {
                name: "Copilot to Ctrl+F12".into(),
                from: "Win+Copilot".into(),
                to: "Ctrl+F12".into(),
                matrix_index: None,
            },
        ];
        let (combos, pending) = build_remap_tables(&remaps);
        assert!(combos.is_empty());
        assert_eq!(pending.len(), 1);
        assert_eq!(pending[0].held_vk, 0x5B); // LWin
        assert_eq!(pending[0].trigger_vk, 0x86); // Copilot
        assert_eq!(pending[0].output_mods, vec![0xA2]); // LCtrl
        assert_eq!(pending[0].output_key, 0x7B); // F12
    }

    #[test]
    fn test_build_tables_firmware_remap_skipped() {
        let remaps = vec![
            RemapConfig {
                name: "Escape to Grave".into(),
                from: "Escape".into(),
                to: "Grave".into(),
                matrix_index: Some(1),
            },
        ];
        let (combos, pending) = build_remap_tables(&remaps);
        assert!(combos.is_empty());
        assert!(pending.is_empty());
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: FAIL — `build_remap_tables` function does not exist.

- [ ] **Step 3: Rename `build_remap_table` to `build_remap_tables` and return both tables**

Replace the `build_remap_table` function with:

```rust
/// Build both remap tables from config entries.
///
/// Returns `(combo_remaps, pending_modifier_remaps)`:
/// - combo_remaps: single key → combo (e.g., CapsLock → Ctrl+F12)
/// - pending_modifier_remaps: combo → key/combo (e.g., Win+L → Delete)
///
/// Classification:
/// - `from` has `+` → combo-source → PendingModifierRemap
/// - `from` single key, `to` has `+` → combo-output → ComboRemap
/// - `from` single key, `to` single key → firmware remap (skipped)
pub fn build_remap_tables(
    remaps: &[crate::config::RemapConfig],
) -> (Vec<ComboRemap>, Vec<PendingModifierRemap>) {
    let mut combo_table = Vec::new();
    let mut pending_table = Vec::new();

    for entry in remaps {
        if entry.from.contains('+') {
            // Combo-source remap: parse from as modifier+trigger
            let (from_mods, from_key) = match keys::parse_key_combo(&entry.from) {
                Some(pair) => pair,
                None => {
                    eprintln!("remap: cannot parse 'from' combo '{}', skipping", entry.from);
                    continue;
                }
            };
            if from_mods.len() != 1 {
                eprintln!(
                    "remap: combo-source '{}' must have exactly one modifier, skipping",
                    entry.from
                );
                continue;
            }

            // Parse `to` as key or combo
            let (to_mods, to_key) = match keys::parse_key_combo(&entry.to) {
                Some(pair) => pair,
                None => {
                    eprintln!("remap: cannot parse 'to' '{}', skipping", entry.to);
                    continue;
                }
            };

            pending_table.push(PendingModifierRemap {
                held_vk: from_mods[0],
                trigger_vk: from_key,
                output_mods: to_mods,
                output_key: to_key,
            });
        } else {
            // Single-key source
            let from_vk = match keys::key_name_to_vk(&entry.from) {
                Some(vk) => vk,
                None => {
                    eprintln!("remap: unknown 'from' key '{}', skipping", entry.from);
                    continue;
                }
            };

            let (mods, key_vk) = match keys::parse_key_combo(&entry.to) {
                Some(pair) => pair,
                None => {
                    eprintln!("remap: cannot parse 'to' combo '{}', skipping", entry.to);
                    continue;
                }
            };

            if mods.is_empty() {
                // Single-key → single-key: firmware remap, skip
                continue;
            }

            combo_table.push(ComboRemap {
                from_vk,
                modifier_vks: mods,
                key_vk,
            });
        }
    }

    (combo_table, pending_table)
}
```

- [ ] **Step 4: Run tests**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: New tests pass. Build may have errors in `main.rs` due to renamed function — that's OK, we fix that in Task 5.

- [ ] **Step 5: Commit**

```bash
git add src/remap.rs
git commit -m "feat: build_remap_tables returns combo + pending-modifier tables"
```

---

### Task 5: Update main.rs to use new remap API

**Files:**
- Modify: `src/main.rs:148-157` (reload_config), `src/main.rs:197-204` (resumed)

- [ ] **Step 1: Update `reload_config` in main.rs**

Replace lines 149-151 (the remap table rebuild section in `reload_config`):

```rust
        // Rebuild remap tables
        let (combo_table, pending_table) = remap::build_remap_tables(&self.config.remap);
        remap::update_remap_table(combo_table);
        remap::update_pending_mod_table(pending_table);
```

- [ ] **Step 2: Update `resumed` in main.rs**

Replace lines 202-204 (the initial remap table build in `resumed`):

```rust
        // Build initial remap tables
        let (combo_table, pending_table) = remap::build_remap_tables(&self.config.remap);
        remap::update_remap_table(combo_table);
        remap::update_pending_mod_table(pending_table);
```

- [ ] **Step 3: Remove old `build_remap_table` (singular) if it still exists**

Check `src/remap.rs` — if the old `build_remap_table` function is still present alongside `build_remap_tables`, delete it. Also delete `update_remap_table` if it's now unused (it should still be used — only the caller changed).

- [ ] **Step 4: Build and test**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: All tests pass, no compile errors.

- [ ] **Step 5: Commit**

```bash
git add src/main.rs src/remap.rs
git commit -m "feat: wire up combo-source remaps in main event loop"
```

---

### Task 6: Update default config with Lock and Copilot remaps

**Files:**
- Modify: `src/config.rs:29-42` (DEFAULT_CONFIG)
- Modify: `%APPDATA%\razer-joro\config.toml` (user's live config)

- [ ] **Step 1: Update DEFAULT_CONFIG in config.rs**

Replace the `DEFAULT_CONFIG` constant with:

```rust
const DEFAULT_CONFIG: &str = r##"# Razer Joro Daemon Config

[lighting]
mode = "static"
color = "#FFFFFF"
brightness = 128

# Key remaps
# from = single key name for firmware/host remap
# from = "Modifier+Key" for combo-source intercept (e.g., keyboard sends Win+L)
# to = single key for simple output, "Modifier+Key" for combo output

[[remap]]
name = "Lock key to Delete"
from = "Win+L"
to = "Delete"

[[remap]]
name = "Copilot key to Ctrl+F12"
from = "Win+Copilot"
to = "Ctrl+F12"

# [[remap]]
# name = "CapsLock to Ctrl+F12"
# from = "CapsLock"
# to = "Ctrl+F12"
# matrix_index = 30
"##;
```

- [ ] **Step 2: Update test_default_config_parses**

In `src/config.rs` tests, update the `test_default_config_parses` test:

```rust
    #[test]
    fn test_default_config_parses() {
        let config: Config = toml::from_str(DEFAULT_CONFIG).unwrap();
        assert_eq!(config.lighting.color, "#FFFFFF");
        assert_eq!(config.lighting.brightness, 128);
        assert_eq!(config.remap.len(), 2);
        assert_eq!(config.remap[0].from, "Win+L");
        assert_eq!(config.remap[0].to, "Delete");
        assert_eq!(config.remap[1].from, "Win+Copilot");
        assert_eq!(config.remap[1].to, "Ctrl+F12");
    }
```

- [ ] **Step 3: Build and test**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: All tests pass.

- [ ] **Step 4: Update user's live config**

Manually add these entries to `%APPDATA%\razer-joro\config.toml` (don't overwrite — append to existing remaps):

```toml
[[remap]]
name = "Lock key to Delete"
from = "Win+L"
to = "Delete"

[[remap]]
name = "Copilot key to Ctrl+F12"
from = "Win+Copilot"
to = "Ctrl+F12"
```

- [ ] **Step 5: Commit**

```bash
git add src/config.rs
git commit -m "feat: default config with Lock→Delete and Copilot→Ctrl+F12 remaps"
```

---

### Task 7: Clean up dead code and verify

**Files:**
- Modify: `src/remap.rs` — remove any leftover references to old types

- [ ] **Step 1: Search for dead code**

Grep for `COMPANION_MODIFIERS`, `PendingCompanion`, `PENDING_COMPANION`, `build_remap_table` (singular, not plural) in `src/remap.rs`. Remove any leftover references.

- [ ] **Step 2: Build and test**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo test" 2>&1`
Expected: All tests pass, no warnings about dead code related to old types.

- [ ] **Step 3: Commit if changes were needed**

```bash
git add src/remap.rs
git commit -m "chore: remove dead companion modifier code"
```

---

### Task 8: Hardware verification

**Files:** None (manual testing)

- [ ] **Step 1: Build release**

Run: `cd L:/PROJECTS/razer-joro && powershell -ExecutionPolicy Bypass -Command ". ./build.ps1; cargo build" 2>&1`

- [ ] **Step 2: Kill Razer services and run daemon**

```
taskkill /F /IM razer_elevation_service.exe
cd C:\Users\mklod\AppData\Local\razer-joro-target\debug
joro-daemon.exe
```

- [ ] **Step 3: Test Lock key → Delete**

Press the Lock key on the Joro keyboard. Expected: Delete character appears (or deletion in text editor). PC should NOT lock.

- [ ] **Step 4: Test Copilot key → Ctrl+F12**

Press the Copilot key. Expected: Ctrl+F12 is sent to the foreground app.

- [ ] **Step 5: Test Win+E still works**

Press Win+E. Expected: File Explorer opens (not intercepted).

- [ ] **Step 6: Test normal typing**

Type normally. Expected: No delays, no missed keys, no phantom modifiers.
