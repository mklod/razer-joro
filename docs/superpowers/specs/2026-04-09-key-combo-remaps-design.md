# Key Combo Remaps — Design Spec

## Goal

Two remap features for the Razer Joro daemon:

1. **Lock key → Delete** — Keyboard's lock key sends firmware Win+L. Intercept and emit Delete instead.
2. **Copilot key → Ctrl+F12** — Fix existing interception so SendInput output actually reaches target apps.

## Change 1: Fix SendInput scan codes

**Problem:** `make_key_input` in `remap.rs` sets `wScan = 0`. Many apps (especially those using DirectInput or raw input) ignore events without scan codes.

**Fix:** Call `MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)` to populate `wScan` in every `KEYBDINPUT` struct. Also add `KEYEVENTF_SCANCODE` flag is NOT needed — just populating `wScan` alongside `wVk` is sufficient for most apps.

**Files:** `src/remap.rs` — `make_key_input` function only.

## Change 2: Combo-source interception (Win+L → Delete)

**Problem:** The lock key sends a real Win+L sequence at firmware level. Current companion state machine only handles "modifier then special VK" patterns where the special VK has a remap entry. Win+L is different — L is a normal key, and the combo itself is what needs remapping.

**Design:** Add a new concept: **combo-source remaps**. These map an incoming key combo (e.g., Win+L) to an output key or combo.

### Config format

```toml
[[remap]]
name = "Lock key to Delete"
from = "Win+L"
to = "Delete"
```

When `from` contains `+`, it's a combo-source remap (handled by the hook). When `from` is a single key, it's either a firmware remap (if `to` is single key with `matrix_index`) or a combo-output remap (if `to` contains `+`).

### Hook implementation

New struct `ComboSourceRemap`:
- `modifier_vk: VkCode` — the modifier to watch for (e.g., LWin 0x5B)
- `trigger_vk: VkCode` — the key that completes the combo (e.g., L 0x4C)
- `output_modifier_vks: Vec<VkCode>` — output modifiers (empty for single key output)
- `output_key_vk: VkCode` — output key (e.g., Delete 0x2E)

State machine in hook_proc:
1. When a modifier in any combo-source remap is pressed, set pending state (suppress the modifier keydown).
2. If the expected trigger key arrives within 50ms: suppress it, emit the output key/combo. On trigger key-up, emit output key-up.
3. If a different key arrives or timeout: replay the modifier keydown, pass through normally.
4. On modifier key-up without trigger: replay modifier down+up.

This extends the existing `PENDING_COMPANION` pattern. Refactor: merge the Copilot companion logic and the new combo-source logic into a single unified pending-modifier state machine, since both follow the same pattern (hold modifier, wait for expected key, act or replay).

### Unified pending-modifier state machine

Replace `COMPANION_MODIFIERS` (static array) and `ComboSourceRemap` with a single `PendingModifierRemap` table:

```rust
struct PendingModifierRemap {
    held_vk: VkCode,        // modifier to hold (e.g., LWin)
    trigger_vk: VkCode,     // key that completes the combo (e.g., 0x86 or L)
    output_mods: Vec<VkCode>, // output modifiers (e.g., [LCtrl])
    output_key: VkCode,     // output key (e.g., F12 or Delete)
}
```

Both Copilot (LWin held → 0x86 trigger → Ctrl+F12 output) and Lock (LWin held → L trigger → Delete output) fit this pattern.

### build_remap_table changes

`build_remap_table` currently returns `Vec<ComboRemap>` (single key → combo). It needs to also return `Vec<PendingModifierRemap>` for combo-source entries. Split:

- `from` has `+` → combo-source → `PendingModifierRemap`
- `from` is single key, `to` has `+` → combo-output → `ComboRemap` (existing)
- `from` is single key, `to` is single key → firmware remap (skip in hook)

## Testing

- Build and run daemon
- Verify Copilot key produces Ctrl+F12 in target app (e.g., VS Code)
- Verify Lock key produces Delete
- Verify normal LWin tap still opens Start menu (if no remap intercepts it — note: since we hold LWin for 50ms waiting for L/0x86, solo LWin tap will be delayed by 50ms before replay, which should still trigger Start menu)
- Verify other Win+X combos (Win+E, Win+R) still work (they will, because only Win+L and Win+0x86 are intercepted)

## Scope

Only `src/remap.rs`, `src/keys.rs` (add `MapVirtualKeyW` import), and `src/config.rs` (no schema change needed — existing `from`/`to` strings already support combo syntax). `src/main.rs` needs minor update to pass both remap tables.
