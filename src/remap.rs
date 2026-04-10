// src/remap.rs — Host-side keyboard hook remap engine
// Last modified: 2026-04-09--2300

use crate::keys::{self, VkCode};
use std::sync::Mutex;
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS,
    KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, VIRTUAL_KEY,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CallNextHookEx, SetWindowsHookExW, UnhookWindowsHookEx, HHOOK, KBDLLHOOKSTRUCT,
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
};

// ── Types ────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone)]
pub struct ComboRemap {
    pub from_vk: VkCode,
    pub modifier_vks: Vec<VkCode>,
    pub key_vk: VkCode,
}

// ── Global state (hook callback is a C function pointer, must be static) ─────

// HHOOK wraps a raw pointer which is !Send. We know it's only accessed under
// a Mutex and only from the hook-install thread, so this wrapper is safe.
struct SendHook(HHOOK);
// SAFETY: We only touch the HHOOK value while holding the Mutex, and the
// hook is always installed/removed on the same thread that owns the event loop.
unsafe impl Send for SendHook {}

static HOOK_HANDLE: Mutex<Option<SendHook>> = Mutex::new(None);
static REMAP_TABLE: Mutex<Vec<ComboRemap>> = Mutex::new(Vec::new());

/// VK codes that accompany remapped keys and need to be suppressed.
/// Maps: companion VK → the remapped VK it precedes.
/// E.g., Copilot sends LWin(0x5B) then 0x86 — so 0x5B is a companion of 0x86.
static COMPANION_MODIFIERS: &[(VkCode, VkCode)] = &[
    (0x5B, 0x86), // LWin accompanies Copilot key
];

/// Tracks whether we're holding a companion modifier, waiting to see if its
/// remapped key follows.
static PENDING_COMPANION: Mutex<Option<PendingCompanion>> = Mutex::new(None);

struct PendingCompanion {
    companion_vk: VkCode,
    expected_vk: VkCode,
    timestamp: std::time::Instant,
}

/// When true, log all key events to stderr (for debugging key identification).
static DEBUG_LOG: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Enable/disable VK debug logging in the hook.
pub fn set_debug_log(enabled: bool) {
    DEBUG_LOG.store(enabled, std::sync::atomic::Ordering::Relaxed);
}

// ── Public API ───────────────────────────────────────────────────────────────

/// Build a remap table from config entries.
/// Only entries whose `to` field resolves to a combo (has modifiers) are added;
/// single-key remaps are handled by firmware and are skipped here.
pub fn build_remap_table(remaps: &[crate::config::RemapConfig]) -> Vec<ComboRemap> {
    let mut table = Vec::new();
    for entry in remaps {
        // Parse the `from` key
        let from_vk = match keys::key_name_to_vk(&entry.from) {
            Some(vk) => vk,
            None => {
                eprintln!("remap: unknown 'from' key '{}', skipping", entry.from);
                continue;
            }
        };

        // Parse the `to` combo
        let (mods, key_vk) = match keys::parse_key_combo(&entry.to) {
            Some(pair) => pair,
            None => {
                eprintln!("remap: cannot parse 'to' combo '{}', skipping", entry.to);
                continue;
            }
        };

        // Only host-side if there are modifiers
        if mods.is_empty() {
            // Single-key remap — firmware handles this, skip
            continue;
        }

        table.push(ComboRemap {
            from_vk,
            modifier_vks: mods,
            key_vk,
        });
    }
    table
}

/// Replace the active remap table.
pub fn update_remap_table(table: Vec<ComboRemap>) {
    *REMAP_TABLE.lock().unwrap() = table;
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

    // Skip injected events (bit 4 = LLKHF_INJECTED = 0x10) to prevent recursion
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

    // --- Companion modifier state machine ---
    // Some keys (e.g., Copilot) send a modifier (LWin) BEFORE the actual key (0x86).
    // We intercept the modifier and hold it, waiting for the expected key.

    // Check if this VK is a companion modifier we should hold
    if is_down {
        for &(companion, expected) in COMPANION_MODIFIERS {
            if vk == companion {
                // Check if the expected key is actually in our remap table
                let table = REMAP_TABLE.lock().unwrap();
                let has_remap = table.iter().any(|r| r.from_vk == expected);
                drop(table);
                if has_remap {
                    *PENDING_COMPANION.lock().unwrap() = Some(PendingCompanion {
                        companion_vk: companion,
                        expected_vk: expected,
                        timestamp: std::time::Instant::now(),
                    });
                    if debug { eprintln!("    holding companion 0x{companion:04X}, expecting 0x{expected:04X}"); }
                    return LRESULT(1); // suppress the companion modifier
                }
            }
        }
    }

    // Check if we have a pending companion
    let pending = PENDING_COMPANION.lock().unwrap().take();
    if let Some(p) = pending {
        let elapsed = p.timestamp.elapsed();
        if vk == p.expected_vk && is_down && elapsed.as_millis() < 100 {
            // The expected key arrived! This is the combo key (e.g., Copilot 0x86).
            // Explicitly release the companion modifier via SendInput to clear OS key state,
            // otherwise Windows thinks it's still held and mangles our combo.
            let inputs = [make_key_input(p.companion_vk, true)]; // key-up
            send_inputs(&inputs);
            if debug { eprintln!("    companion confirmed for 0x{vk:04X}, released 0x{:04X}", p.companion_vk); }
        } else {
            // Something else arrived, or timeout. Replay the companion modifier.
            if debug { eprintln!("    companion expired (got 0x{vk:04X}), replaying 0x{:04X}", p.companion_vk); }
            let inputs = [make_key_input(p.companion_vk, false)];
            send_inputs(&inputs);
            // Fall through to process current key normally
        }
    }

    // Also handle companion key-up: suppress the up event for the companion
    // when it was already suppressed on key-down
    if is_up {
        for &(companion, _) in COMPANION_MODIFIERS {
            if vk == companion {
                // Check if the companion's expected key is in the remap table
                // If so, suppress the key-up too
                let table = REMAP_TABLE.lock().unwrap();
                let active = table.iter().any(|r| {
                    COMPANION_MODIFIERS.iter().any(|&(c, e)| c == vk && r.from_vk == e)
                });
                drop(table);
                if active {
                    return LRESULT(1); // suppress companion key-up
                }
            }
        }
    }

    // --- Standard remap lookup ---
    let table = REMAP_TABLE.lock().unwrap();
    let found = table.iter().find(|r| r.from_vk == vk).cloned();
    drop(table);

    if let Some(remap) = found {
        if is_down {
            if debug { eprintln!("    -> remap: mods={:?} key=0x{:04X}", remap.modifier_vks, remap.key_vk); }
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
                wScan: 0,
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
