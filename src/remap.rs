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

    // Look up in remap table
    let table = REMAP_TABLE.lock().unwrap();
    let found = table.iter().find(|r| r.from_vk == vk).cloned();
    drop(table);

    if let Some(remap) = found {
        if is_down {
            send_combo_down(&remap.modifier_vks, remap.key_vk);
            return LRESULT(1); // suppress original key
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

fn make_key_input(vk: VkCode, key_up: bool) -> INPUT {
    let mut flags = KEYEVENTF_EXTENDEDKEY;
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
