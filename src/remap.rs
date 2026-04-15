// src/remap.rs — Host-side keyboard hook remap engine
// Last modified: 2026-04-10--0115
//
// Architecture: "modifier gate"
//
// For combo-source remaps (keyboard sends Win+L, Win+0x86, etc.), we gate the
// modifier key (Win) on keydown and resolve on the VERY NEXT keypress:
//   - Trigger key (L, 0x86) → fire remap
//   - Non-trigger key (E, D, ...) → replay modifier, pass key through
//   - Modifier key-up (Win↑) → replay modifier tap (Start menu opens normally)
//
// No timeout. No state machine. Resolves on the next keypress, which is <1ms
// for firmware macros and instant for non-trigger keys.
//
// For Copilot key (LShift↓, LWin↓, 0x86↓), LShift arrives before LWin. We
// suppress LShift too (it's a known "prefix mod" for this trigger).
//
// Crash safety: if daemon dies while modifier is gated, re-tapping that key
// physically fixes it. The gated window is typically <1ms for firmware macros.

use crate::keys::{self, VkCode};
use std::sync::Mutex;
use std::io::Write;
use windows::Win32::Foundation::{LPARAM, LRESULT, WPARAM};
use windows::Win32::UI::Input::KeyboardAndMouse::{
    MapVirtualKeyW, SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT,
    KEYBD_EVENT_FLAGS, KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, MAP_VIRTUAL_KEY_TYPE, VIRTUAL_KEY,
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

/// Host-side Fn-layer remap. Source is one VK pressed while `fn_detect::FN_HELD`
/// is true; output is modifier(s) + key. This is the daemon's equivalent of
/// firmware Hypershift, usable over BLE where firmware writes are unavailable.
#[derive(Debug, Clone)]
pub struct FnHostRemap {
    pub from_vk: VkCode,
    pub modifier_vks: Vec<VkCode>,
    pub key_vk: VkCode,
}

/// Trigger-based remap for combo-source keys (e.g., Lock key sends Win+L → Delete).
#[derive(Debug, Clone)]
pub struct TriggerRemap {
    /// The modifier that gets gated (suppressed until resolved)
    pub gate_mod_vk: VkCode,
    /// The trigger key that fires the remap
    pub trigger_vk: VkCode,
    /// Extra modifier VKs sent by the keyboard before the gate mod (e.g., LShift for Copilot).
    /// These are suppressed when they arrive during the prefix window.
    pub prefix_mods: Vec<VkCode>,
    /// Output modifier(s) to inject (e.g., Ctrl for Ctrl+F12)
    pub output_mods: Vec<VkCode>,
    /// Output key to inject
    pub output_key: VkCode,
}

// ── Injection tagging ────────────────────────────────────────────────────────
//
// Our SendInput calls arrive back at WH_KEYBOARD_LL with LLKHF_INJECTED set,
// same as any other synthetic event. Windows itself injects media-key VKs
// (VK_VOLUME_MUTE, VK_VOLUME_DOWN, VK_VOLUME_UP, VK_MEDIA_PLAY_PAUSE, etc.)
// when it processes Consumer Control HID reports — those also arrive with
// LLKHF_INJECTED set. We need to process the Windows-native injections (so
// the user can remap VolumeMute → F5) while skipping our own injections
// (to prevent recursion).
//
// Tag our own events with a magic `dwExtraInfo` value and treat only events
// matching the tag as "ours to skip". Windows-native injections always have
// dwExtraInfo=0.
const OUR_INJECTION_TAG: usize = 0x4A6F524F; // 'JoRO' little-endian magic

// ── Global state (hook callback is a C function pointer, must be static) ─────

struct SendHook(HHOOK);
unsafe impl Send for SendHook {}

static HOOK_HANDLE: Mutex<Option<SendHook>> = Mutex::new(None);
static REMAP_TABLE: Mutex<Vec<ComboRemap>> = Mutex::new(Vec::new());
static TRIGGER_TABLE: Mutex<Vec<TriggerRemap>> = Mutex::new(Vec::new());
static FN_HOST_REMAP_TABLE: Mutex<Vec<FnHostRemap>> = Mutex::new(Vec::new());

/// Currently-held Fn-layer remap (if any). Set on Fn+key key-down, cleared on
/// the source-VK key-up. Tracks which output combo we emitted so we release
/// the same combo even if Fn was released first.
static ACTIVE_FN_REMAP: Mutex<Option<ActiveFnRemap>> = Mutex::new(None);

#[derive(Clone)]
struct ActiveFnRemap {
    from_vk: VkCode,
    output_mods: Vec<VkCode>,
    output_key: VkCode,
}

/// Modifier gate state. When a gated modifier is pressed, we suppress it and
/// wait for the next key to decide what to do.
static GATE: Mutex<Option<GateState>> = Mutex::new(None);

#[derive(Clone)]
struct GateState {
    /// The modifier VK we suppressed
    gate_vk: VkCode,
    /// Prefix mods we also suppressed (e.g., LShift for Copilot)
    suppressed_prefix: Vec<VkCode>,
}

/// Track which output combo is currently "held down" so we can release it on
/// the trigger's key-up. Only one trigger remap can be active at a time.
static ACTIVE_TRIGGER: Mutex<Option<ActiveTrigger>> = Mutex::new(None);

#[derive(Clone)]
struct ActiveTrigger {
    trigger_vk: VkCode,
    /// The gate modifier that was suppressed — suppress its key-up too
    gate_vk: VkCode,
    /// Prefix mods that were suppressed — suppress their key-ups too
    suppressed_prefix: Vec<VkCode>,
    output_mods: Vec<VkCode>,
    output_key: VkCode,
    /// True after trigger key-up sent combo_up. We keep the ActiveTrigger
    /// alive until gate_vk key-up arrives so we can suppress it.
    output_released: bool,
}

/// When true, log key events to a file for debugging.
static DEBUG_LOG: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Enable/disable VK debug logging in the hook.
#[allow(dead_code)]
pub fn set_debug_log(enabled: bool) {
    DEBUG_LOG.store(enabled, std::sync::atomic::Ordering::Relaxed);
}

/// Write a debug line to the log file. Opened in append mode each time to keep
/// the hook callback fast (no persistent file handle needing synchronization).
fn dbg_log(msg: &str) {
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(r"C:\Users\mklod\AppData\Local\razer-joro-target\hook_debug.log")
    {
        let _ = writeln!(f, "{msg}");
    }
}

// ── Public API ───────────────────────────────────────────────────────────────

/// Build both remap tables from config entries.
///
/// Returns `(combo_remaps, trigger_remaps)`:
/// - combo_remaps: single key → combo (e.g., CapsLock → Ctrl+F12)
/// - trigger_remaps: combo-source → key/combo (e.g., Win+L → Delete)
///
/// Classification:
/// - `from` has `+` → combo-source → TriggerRemap
/// - `from` single key, `to` has `+` → combo-output → ComboRemap
/// - `from` single key, `to` single key → firmware remap (skipped)
pub fn build_remap_tables(
    remaps: &[crate::config::RemapConfig],
) -> (Vec<ComboRemap>, Vec<TriggerRemap>) {
    let mut combo_table = Vec::new();
    let mut trigger_table = Vec::new();

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

            let prefix_mods = determine_prefix_mods(from_mods[0], from_key);

            trigger_table.push(TriggerRemap {
                gate_mod_vk: from_mods[0],
                trigger_vk: from_key,
                prefix_mods,
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

            // mods may be empty — that's a plain single-key → single-key remap
            // (e.g. "a" → "b"). The hook's send_combo_down/up helpers handle an
            // empty modifier list correctly (iterate zero mods, then send key).
            // Previously this branch skipped with the intent of letting firmware
            // handle it via matrix_index, but BLE has no firmware keymaps and
            // USB firmware remaps also require matrix_index — so in practice
            // this was silently dropping user-configured remaps.
            combo_table.push(ComboRemap {
                from_vk,
                modifier_vks: mods,
                key_vk,
            });
        }
    }

    (combo_table, trigger_table)
}

/// Determine prefix modifier VKs sent before the gate modifier.
/// Copilot key sends LShift↓ before LWin↓ — LShift is a prefix mod.
fn determine_prefix_mods(gate_mod: VkCode, trigger: VkCode) -> Vec<VkCode> {
    // Copilot key (VK 0x86) sends: LShift↓, LWin↓, 0x86↓
    if trigger == 0x86 && gate_mod == 0x5B {
        vec![0xA0] // LShift
    } else {
        vec![]
    }
}

/// Replace the active remap table.
pub fn update_remap_table(table: Vec<ComboRemap>) {
    *REMAP_TABLE.lock().unwrap() = table;
}

/// Replace the active trigger remap table.
pub fn update_trigger_table(table: Vec<TriggerRemap>) {
    *TRIGGER_TABLE.lock().unwrap() = table;
}

/// Replace the active Fn-layer host remap table.
pub fn update_fn_host_remap_table(table: Vec<FnHostRemap>) {
    *FN_HOST_REMAP_TABLE.lock().unwrap() = table;
}

/// Build a Fn-layer host remap table from config entries. `from` must be a
/// single key name (not a combo) since Fn is already the modifier. `to` can
/// be a single key or a combo. Entries with unparseable keys are logged and
/// skipped.
pub fn build_fn_host_remap_table(
    entries: &[crate::config::FnRemapConfig],
) -> Vec<FnHostRemap> {
    let mut out = Vec::new();
    for entry in entries {
        let from_vk = match keys::key_name_to_vk(entry.from.trim()) {
            Some(vk) => vk,
            None => {
                eprintln!(
                    "fn_host_remap: unknown source key '{}', skipping",
                    entry.from
                );
                continue;
            }
        };
        let (mods, key_vk) = match keys::parse_key_combo(entry.to.trim()) {
            Some(pair) => pair,
            None => {
                eprintln!(
                    "fn_host_remap: cannot parse output '{}', skipping",
                    entry.to
                );
                continue;
            }
        };
        out.push(FnHostRemap {
            from_vk,
            modifier_vks: mods,
            key_vk,
        });
    }
    out
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

/// Remove the installed hook (if any). Releases all modifier keys to prevent
/// stuck keyboard state.
pub fn remove_hook() {
    // Release all modifiers before unhooking to prevent stuck keys
    release_all_modifiers();
    // Clear all state
    *GATE.lock().unwrap() = None;
    *ACTIVE_TRIGGER.lock().unwrap() = None;

    let handle = HOOK_HANDLE.lock().unwrap().take();
    if let Some(SendHook(h)) = handle {
        unsafe {
            let _ = UnhookWindowsHookEx(h);
        }
    }
}

/// Release all modifier keys via SendInput. Called on shutdown to prevent
/// stuck keyboard state.
fn release_all_modifiers() {
    let inputs: Vec<INPUT> = [0xA0u16, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C]
        .iter()
        .map(|&vk| make_key_input(vk, true))
        .collect();
    send_inputs(&inputs);
}

// ── Hook callback ────────────────────────────────────────────────────────────

unsafe extern "system" fn hook_proc(code: i32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if code < 0 {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    let kb = &*(lparam.0 as *const KBDLLHOOKSTRUCT);
    let vk = kb.vkCode as VkCode;
    let msg = wparam.0 as u32;
    let is_down = msg == WM_KEYDOWN || msg == WM_SYSKEYDOWN;
    let is_up = msg == WM_KEYUP || msg == WM_SYSKEYUP;
    let debug = DEBUG_LOG.load(std::sync::atomic::Ordering::Relaxed);
    let injected = kb.flags.0 & 0x10 != 0;

    // Log ALL events (including injected) before any processing
    if debug && (is_down || is_up) {
        let dir = if is_down { "DN" } else { "UP" };
        let inj = if injected {
            if kb.dwExtraInfo == OUR_INJECTION_TAG { " INJ=ours" } else { " INJ=win" }
        } else { "" };
        dbg_log(&format!("{dir} vk=0x{vk:04X} scan=0x{:04X}{inj}", kb.scanCode));
    }

    // Skip ONLY our own injected events (tagged via dwExtraInfo) to prevent
    // recursion. Windows-native injections (media VKs generated from HID
    // Consumer Control reports) leave dwExtraInfo=0 and MUST be processed
    // normally so media-to-function-key remaps can fire.
    if injected && kb.dwExtraInfo == OUR_INJECTION_TAG {
        return CallNextHookEx(None, code, wparam, lparam);
    }

    // ── Fn-layer host remap (Hypershift over BLE) ────────────────────────────
    //
    // When `fn_detect::FN_HELD` is true, the user is holding the Razer Fn key.
    // Joro's firmware Fn layer may or may not be active — if it is, Fn+Left
    // already emits VK_HOME via firmware and arrives here as 0x24. If not,
    // Fn+Left arrives as VK_LEFT (0x25) and we translate host-side per the
    // user's `[[fn_host_remap]]` config. Active remaps are tracked in
    // `ACTIVE_FN_REMAP` so the source key-up still releases the right combo
    // even if Fn was released first.
    {
        // Handle source key-up for an in-flight Fn remap first (regardless
        // of current FN_HELD state).
        if is_up {
            let active = ACTIVE_FN_REMAP.lock().unwrap().clone();
            if let Some(ref a) = active {
                if a.from_vk == vk {
                    if debug {
                        dbg_log(&format!(
                            "  ACT: fn-remap up 0x{vk:04X} -> release {:?}+0x{:04X}",
                            a.output_mods, a.output_key
                        ));
                    }
                    send_combo_up(&a.output_mods, a.output_key);
                    *ACTIVE_FN_REMAP.lock().unwrap() = None;
                    return LRESULT(1);
                }
            }
        }
        // On key-down while Fn is held, check the fn_host_remap table.
        if is_down && crate::fn_detect::fn_held() {
            let table = FN_HOST_REMAP_TABLE.lock().unwrap();
            let matched = table.iter().find(|r| r.from_vk == vk).cloned();
            drop(table);
            if let Some(r) = matched {
                if debug {
                    dbg_log(&format!(
                        "  ACT: fn-remap down 0x{vk:04X} -> emit {:?}+0x{:04X}",
                        r.modifier_vks, r.key_vk
                    ));
                }
                send_combo_down(&r.modifier_vks, r.key_vk);
                *ACTIVE_FN_REMAP.lock().unwrap() = Some(ActiveFnRemap {
                    from_vk: vk,
                    output_mods: r.modifier_vks,
                    output_key: r.key_vk,
                });
                return LRESULT(1);
            }
        }
    }

    // ── Active trigger: suppress key-ups for gate mod, prefix mods, and trigger ──
    //
    // Two-phase release:
    //   1. Trigger↑ arrives → send combo_up, set output_released=true (keep active)
    //   2. Gate mod↑ arrives → suppress it, clear active trigger entirely
    // This ensures the orphan gate mod↑ (e.g., Win↑) is always suppressed.
    // Either key-up can arrive first (Lock key: trigger↑ before gate↑;
    // Copilot: gate↑ before trigger↑).
    {
        let active = ACTIVE_TRIGGER.lock().unwrap().clone();
        if let Some(ref a) = active {
            if is_up {
                // Suppress prefix mod key-ups (e.g., LShift from Copilot)
                if a.suppressed_prefix.contains(&vk) {
                    if debug { dbg_log(&format!("  ACT: suppress prefix up 0x{vk:04X}")); }
                    return LRESULT(1);
                }
                // Gate mod key-up (e.g., LWin)
                if vk == a.gate_vk {
                    if !a.output_released {
                        // Gate released before trigger (firmware sends Win↑ before L↑).
                        // Send combo_up now but KEEP active trigger alive so we can
                        // still suppress the upcoming trigger key-up.
                        if debug { dbg_log(&format!("  ACT: gate up 0x{vk:04X}, combo_up, keep active for trigger")); }
                        send_combo_up(&a.output_mods, a.output_key);
                        let mut updated = a.clone();
                        updated.output_released = true;
                        *ACTIVE_TRIGGER.lock().unwrap() = Some(updated);
                    } else {
                        // Both trigger↑ and gate↑ seen — fully clear.
                        // Inject cleanup key-ups to force-release modifiers that
                        // LRESULT(1) suppression didn't fully clear from Windows state.
                        if debug { dbg_log(&format!("  ACT: gate up 0x{vk:04X}, clear + cleanup")); }
                        cleanup_modifiers(&a);
                        *ACTIVE_TRIGGER.lock().unwrap() = None;
                    }
                    return LRESULT(1);
                }
                // Trigger key-up
                if vk == a.trigger_vk {
                    if !a.output_released {
                        // Trigger released before gate (manual Win+J: J↑ before Win↑).
                        // Send combo_up, keep active for gate key-up suppression.
                        if debug { dbg_log(&format!("  ACT: trigger up 0x{vk:04X}, combo_up, keep active for gate")); }
                        send_combo_up(&a.output_mods, a.output_key);
                        let mut updated = a.clone();
                        updated.output_released = true;
                        *ACTIVE_TRIGGER.lock().unwrap() = Some(updated);
                    } else {
                        // Both trigger↑ and gate↑ seen — fully clear.
                        if debug { dbg_log(&format!("  ACT: trigger up 0x{vk:04X}, clear + cleanup")); }
                        cleanup_modifiers(&a);
                        *ACTIVE_TRIGGER.lock().unwrap() = None;
                    }
                    return LRESULT(1);
                }
            }
            // Suppress gate mod key-down repeats (firmware sends Win↓ repeatedly
            // while Lock key is held — don't let these fall through to the gate logic)
            if is_down && vk == a.gate_vk {
                return LRESULT(1);
            }
            // Suppress prefix mod key-down repeats
            if is_down && a.suppressed_prefix.contains(&vk) {
                return LRESULT(1);
            }
            // Trigger key-down repeat while active → send output key repeat
            if is_down && vk == a.trigger_vk && !a.output_released {
                send_inputs(&[make_key_input(a.output_key, false)]);
                return LRESULT(1);
            }
            // Suppress trigger repeats after output released (cleanup phase)
            if is_down && vk == a.trigger_vk {
                return LRESULT(1);
            }
        }
    }

    // ── Gate: resolve pending gated modifier ─────────────────────────────────
    //
    // If the gate is active, the NEXT keypress decides:
    //   - Trigger key → fire remap
    //   - Prefix mod (e.g., LShift before Copilot's LWin) → suppress, keep waiting
    //   - Gate mod key-up → user just tapped Win, replay tap for Start menu
    //   - Any other key → replay gate mod, pass key through (Win+E etc.)
    {
        let gate = GATE.lock().unwrap().take();
        if let Some(g) = gate {
            // Check if this is a trigger key
            if is_down {
                let table = TRIGGER_TABLE.lock().unwrap();
                let matched = table.iter()
                    .find(|r| r.gate_mod_vk == g.gate_vk && r.trigger_vk == vk)
                    .cloned();
                drop(table);

                if let Some(remap) = matched {
                    // TRIGGER MATCHED — fire the remap
                    if debug {
                        dbg_log(&format!("  ACT: gate trigger 0x{vk:04X} -> emit {:?}+0x{:04X}",
                            remap.output_mods, remap.output_key));
                    }

                    // Cancel any prefix mods that leaked through before the gate.
                    // Copilot sends LShift↓ before LWin↓ — LShift passed through
                    // because we can't suppress it before knowing a gate will follow.
                    // Inject key-ups now and mark them for key-up suppression later.
                    let mut all_prefix = g.suppressed_prefix.clone();
                    for &pm in &remap.prefix_mods {
                        if !all_prefix.contains(&pm) {
                            // This prefix leaked through (arrived before gate)
                            send_inputs(&[make_key_input(pm, true)]); // cancel it
                            all_prefix.push(pm);
                        }
                    }

                    send_combo_down(&remap.output_mods, remap.output_key);
                    *ACTIVE_TRIGGER.lock().unwrap() = Some(ActiveTrigger {
                        trigger_vk: vk,
                        gate_vk: g.gate_vk,
                        suppressed_prefix: all_prefix,
                        output_mods: remap.output_mods.clone(),
                        output_key: remap.output_key,
                        output_released: false,
                    });
                    return LRESULT(1); // suppress trigger key-down
                }

                // Check if this is a known prefix modifier (e.g., LShift before Copilot)
                let table = TRIGGER_TABLE.lock().unwrap();
                let is_prefix = table.iter()
                    .any(|r| r.gate_mod_vk == g.gate_vk && r.prefix_mods.contains(&vk));
                drop(table);

                if is_prefix {
                    // Prefix mod — suppress and keep waiting
                    if debug { dbg_log(&format!("  ACT: prefix mod 0x{vk:04X}, keep gating")); }
                    let mut new_g = g;
                    new_g.suppressed_prefix.push(vk);
                    *GATE.lock().unwrap() = Some(new_g);
                    return LRESULT(1);
                }

                // NON-TRIGGER KEY — replay gated modifier and pass key through
                if debug {
                    dbg_log(&format!("  ACT: gate broken by 0x{vk:04X}, replay mod 0x{:04X}", g.gate_vk));
                }
                // Replay any suppressed prefix mods first, then the gate mod
                let mut replay: Vec<INPUT> = Vec::new();
                for &prefix in &g.suppressed_prefix {
                    replay.push(make_key_input(prefix, false));
                }
                replay.push(make_key_input(g.gate_vk, false));
                send_inputs(&replay);
                // Fall through — let this key be processed normally
            } else if is_up && vk == g.gate_vk {
                // GATE MOD KEY-UP — user just tapped Win. Replay full tap.
                if debug { dbg_log("  ACT: gate mod released, replay tap"); }
                let mut replay: Vec<INPUT> = Vec::new();
                for &prefix in &g.suppressed_prefix {
                    replay.push(make_key_input(prefix, false));
                    replay.push(make_key_input(prefix, true));
                }
                replay.push(make_key_input(g.gate_vk, false));
                replay.push(make_key_input(g.gate_vk, true));
                send_inputs(&replay);
                return LRESULT(1); // suppress the real key-up (we replayed it)
            } else if is_up && g.suppressed_prefix.contains(&vk) {
                // Prefix mod released before gate mod — unusual but handle it.
                // Replay everything.
                if debug { dbg_log("  ACT: prefix released before trigger, replay"); }
                let mut replay: Vec<INPUT> = Vec::new();
                for &prefix in &g.suppressed_prefix {
                    replay.push(make_key_input(prefix, false));
                }
                replay.push(make_key_input(g.gate_vk, false));
                send_inputs(&replay);
                // Don't suppress this key-up — let it through after replay
                return CallNextHookEx(None, code, wparam, lparam);
            } else {
                // Some other key-up while gated — put gate back
                *GATE.lock().unwrap() = Some(g);
            }
        }
    }

    // ── Check if this key-down should activate the gate ──────────────────────
    if is_down {
        let table = TRIGGER_TABLE.lock().unwrap();
        let should_gate = table.iter().any(|r| r.gate_mod_vk == vk);
        drop(table);

        if should_gate {
            // Only gate on initial press, not auto-repeat
            let gate = GATE.lock().unwrap();
            if gate.is_none() {
                drop(gate);
                if debug { dbg_log(&format!("  ACT: gating 0x{vk:04X}")); }
                *GATE.lock().unwrap() = Some(GateState {
                    gate_vk: vk,
                    suppressed_prefix: vec![],
                });
                return LRESULT(1); // suppress the modifier
            }
        }
    }

    // ── Check if this is a prefix mod arriving BEFORE the gate mod ───────────
    // Copilot sends LShift↓ before LWin↓. We need to suppress LShift so it
    // doesn't leak through. We only do this if a trigger remap has this VK
    // as a prefix_mod.
    if is_down {
        let table = TRIGGER_TABLE.lock().unwrap();
        let is_prefix = table.iter().any(|r| r.prefix_mods.contains(&vk));
        drop(table);

        if is_prefix {
            // Only suppress if no gate is active yet (prefix arrives before gate mod)
            let gate = GATE.lock().unwrap();
            if gate.is_none() {
                drop(gate);
                // Don't suppress yet — we don't know if the gate mod will follow.
                // If we suppress LShift and the user is just typing Shift+A, bad.
                // Instead, we let it through. The gate will handle cleanup if needed.
            }
        }
    }

    // ── Standard single-key remap lookup ─────────────────────────────────────
    let table = REMAP_TABLE.lock().unwrap();
    let found = table.iter().find(|r| r.from_vk == vk).cloned();
    drop(table);

    if let Some(remap) = found {
        if is_down {
            if debug {
                dbg_log(&format!("  ACT: remap mods={:?} key=0x{:04X}", remap.modifier_vks, remap.key_vk));
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

/// After a trigger remap completes, inject key-up events for the gated modifier
/// (and any prefix mods). LRESULT(1) suppression in WH_KEYBOARD_LL doesn't fully
/// clear Windows' internal key state — this ensures modifiers are actually released.
fn cleanup_modifiers(a: &ActiveTrigger) {
    let mut inputs: Vec<INPUT> = Vec::new();
    for &prefix in &a.suppressed_prefix {
        inputs.push(make_key_input(prefix, true));
    }
    inputs.push(make_key_input(a.gate_vk, true));
    send_inputs(&inputs);
}

fn send_combo_down(modifier_vks: &[VkCode], key_vk: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    for &m in modifier_vks {
        inputs.push(make_key_input(m, false));
    }
    inputs.push(make_key_input(key_vk, false));
    send_inputs(&inputs);
}

fn send_combo_up(modifier_vks: &[VkCode], key_vk: VkCode) {
    let mut inputs: Vec<INPUT> = Vec::new();
    inputs.push(make_key_input(key_vk, true));
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

pub(crate) fn make_key_input(vk: VkCode, key_up: bool) -> INPUT {
    let scan = unsafe {
        MapVirtualKeyW(vk as u32, MAP_VIRTUAL_KEY_TYPE(0)) as u16
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
                // Tag so our hook's injection filter can skip our own events
                // while still processing Windows-native injections.
                dwExtraInfo: OUR_INJECTION_TAG,
            },
        },
    }
}

pub(crate) fn send_inputs(inputs: &[INPUT]) {
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
        let (combos, triggers) = build_remap_tables(&remaps);
        assert!(combos.is_empty());
        assert_eq!(triggers.len(), 1);
        assert_eq!(triggers[0].gate_mod_vk, 0x5B); // LWin
        assert_eq!(triggers[0].trigger_vk, 0x4C); // L
        assert_eq!(triggers[0].prefix_mods, Vec::<VkCode>::new());
        assert_eq!(triggers[0].output_mods, Vec::<VkCode>::new());
        assert_eq!(triggers[0].output_key, 0x2E); // Delete
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
        let (combos, triggers) = build_remap_tables(&remaps);
        assert_eq!(combos.len(), 1);
        assert_eq!(combos[0].from_vk, 0x14); // CapsLock
        assert_eq!(combos[0].modifier_vks, vec![0xA2]); // LCtrl
        assert_eq!(combos[0].key_vk, 0x7B); // F12
        assert!(triggers.is_empty());
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
        let (combos, triggers) = build_remap_tables(&remaps);
        assert!(combos.is_empty());
        assert_eq!(triggers.len(), 1);
        assert_eq!(triggers[0].gate_mod_vk, 0x5B); // LWin
        assert_eq!(triggers[0].trigger_vk, 0x86); // Copilot
        assert_eq!(triggers[0].prefix_mods, vec![0xA0]); // LShift
        assert_eq!(triggers[0].output_mods, vec![0xA2]); // LCtrl
        assert_eq!(triggers[0].output_key, 0x7B); // F12
    }

    #[test]
    fn test_build_tables_single_to_single() {
        // Single-key → single-key remap is handled host-side with an empty
        // modifier list. Previously this was skipped (intended for firmware
        // keymap path), but BLE has no firmware keymaps and USB never wired
        // it up, so user-configured remaps were silently dropped.
        let remaps = vec![
            RemapConfig {
                name: "a to b".into(),
                from: "a".into(),
                to: "b".into(),
                matrix_index: None,
            },
        ];
        let (combos, triggers) = build_remap_tables(&remaps);
        assert_eq!(combos.len(), 1);
        assert_eq!(combos[0].from_vk, 0x41); // 'A'
        assert!(combos[0].modifier_vks.is_empty());
        assert_eq!(combos[0].key_vk, 0x42); // 'B'
        assert!(triggers.is_empty());
    }

    #[test]
    fn test_prefix_mods_copilot() {
        let prefix = determine_prefix_mods(0x5B, 0x86);
        assert_eq!(prefix, vec![0xA0]); // LShift
    }

    #[test]
    fn test_prefix_mods_lock() {
        let prefix = determine_prefix_mods(0x5B, 0x4C);
        assert!(prefix.is_empty());
    }
}
