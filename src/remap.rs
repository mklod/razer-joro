// src/remap.rs — Host-side keyboard hook remap engine
// Last modified: 2026-04-09--2358

use crate::keys::{self, VkCode};
use std::sync::Mutex;
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MAP_VIRTUAL_KEY_TYPE, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, SetWindowsHookExW, UnhookWindowsHookEx, HHOOK, KBDLLHOOKSTRUCT,
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

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

/// When true, log all key events to stderr (for debugging key identification).
static DEBUG_LOG: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Enable/disable VK debug logging in the hook.
pub fn set_debug_log(enabled: bool) {
    DEBUG_LOG.store(enabled, std::sync::atomic::Ordering::Relaxed);
}

// ── Public API ───────────────────────────────────────────────────────────────

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

/// Compatibility shim: delegates to `build_remap_tables`, returns only the combo table.
/// Deprecated — callers should migrate to `build_remap_tables`.
#[allow(dead_code)]
pub fn build_remap_table(remaps: &[crate::config::RemapConfig]) -> Vec<ComboRemap> {
    build_remap_tables(remaps).0
}

/// Replace the active remap table.
pub fn update_remap_table(table: Vec<ComboRemap>) {
    *REMAP_TABLE.lock().unwrap() = table;
}

/// Replace the active pending-modifier remap table.
pub fn update_pending_mod_table(table: Vec<PendingModifierRemap>) {
    *PENDING_MOD_TABLE.lock().unwrap() = table;
}

/// Install the low-level keyboard hook on the current thread.
pub fn install_hook() -> Result<(), String> {
    let hook = unsafe {
        SetWindowsHookExW(WH_KEYBOARD_LL, Some(hook_proc), None, 0)
            .map_err(|e| format!("SetWindowsHookExW failed: {e}"))?
    };
    *HOOK_HANDLE.lock().unwrap() = Some(SendHook(hook));
    Ok(())
}

/// Remove the installed hook (if any).
pub fn remove_hook() {
    let handle = HOOK_HANDLE.lock().unwrap().take();
    if let Some(SendHook(h)) = handle {
        unsafe {
            let _ = UnhookWindowsHookEx(h);
        }
    }
}

// ── Hook callback ────────────────────────────────────────────────────────────

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
        if is_up && (vk == held_vk || vk == get_confirmed_trigger(held_vk)) {
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

// ── SendInput helpers ────────────────────────────────────────────────────────

/// Look up the trigger VK associated with a held modifier from the pending-mod table.
fn get_confirmed_trigger(held_vk: VkCode) -> VkCode {
    let table = PENDING_MOD_TABLE.lock().unwrap();
    table.iter()
        .find(|r| r.held_vk == held_vk)
        .map(|r| r.trigger_vk)
        .unwrap_or(0)
}

fn send_combo_down(modifier_vks: &[VkCode], key_vk: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    // Press each modifier
    for &m in modifier_vks {
        inputs.push(make_key_input(m, false));
    }
    // Press the key
    inputs.push(make_key_input(key_vk, false));
    send_inputs(&inputs);
}

fn send_combo_up(modifier_vks: &[VkCode], key_vk: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    // Release the key first
    inputs.push(make_key_input(key_vk, true));
    // Release modifiers in reverse order
    for &m in modifier_vks.iter().rev() {
        inputs.push(make_key_input(m, true));
    }
    send_inputs(&inputs);
}

fn is_extended_key(vk: VkCode) -> bool {
    matches!(vk,
        0x21..=0x28 | // PgUp, PgDn, End, Home, Arrows
        0x2D..=0x2E | // Insert, Delete
        0x5B..=0x5C | // LWin, RWin
        0x5D |        // Apps
        0xA3 |        // RCtrl
        0xA5 |        // RAlt
        0x6F |        // Numpad /
        0x90 |        // NumLock
        0x2C          // PrintScreen
    )
}

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

fn send_inputs(inputs: &[INPUT]) {
    if inputs.is_empty() {
        return;
    }
    let cb = std::mem::size_of::<INPUT>() as i32;
    unsafe {
        SendInput(inputs, cb);
    }
}

// ── Tests ────────────────────────────────────────────────────────────────────

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
