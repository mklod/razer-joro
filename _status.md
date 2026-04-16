# Razer Joro — Status

## Session 2026-04-16--0008 — DDC/CI handle caching, fn_detect BLE reconnect fix

**Late-session fixes (post-commit amendments):**

- **DDC/CI monitor handle caching** — `PhysicalMonitor` handle is now stored in a global `BRIGHTNESS_STATE` mutex and reused across presses. Previously `enumerate()` was called on every keypress, which invoked `GetMonitorBrightness` (a separate dxva2 DDC/CI read transaction) before each stepped write. The Falcon's scaler firmware rebooted under sustained DDC/CI read+write interleaving. With caching, the read happens exactly once; subsequent presses do only `SetVCPFeature` writes. Write failures (stale handle after a monitor reboot/re-enumeration) auto-invalidate the cache so the next press re-enumerates cleanly. Step delay bumped from 5ms → 20ms for additional stability.
- **fn_detect BLE reconnect fix** — new `fn_detect::reset()` clears the tracked-paths set. Called from `try_connect` before `start()` on every BLE reconnection. Without this, old HID collection handles go stale after a BLE disconnect/reconnect cycle (Windows creates new device paths for the reconnected keyboard) and fn_detect's reader threads spin on dead handles forever. Fn+key Hypershift remaps silently stopped working after any BLE hiccup. Fixed by forcing re-enumeration of all HID collections on each connect.

**Current state:** daemon is stable. All key remaps, brightness, backlight, Hypershift, Lock/Copilot, F4 Win+Tab all working. Monitor brightness dims without rebooting (tested 10+ cycles).

**Next session priorities (unchanged):**
1. Hypershift matrix gap scan (#12) — USB required
2. Remaps not firing in webview popover (#13) — WebView2 filtering SendInput

## Session 2026-04-15--2123 — gate-broken atomic replay, F4/F10/F11 MM-locked, tray icon, startup cleanup

**Bug fixes:**
- **Gate-broken atomic replay** — when a non-trigger key (e.g. Tab from F4's firmware Win+Tab macro) breaks a gated modifier (Win), the gate now replays modifier+key as a **single atomic SendInput batch** and suppresses the original event. Previously the gate replayed Win as a separate injection then fell through for Tab — but Win↑ from the firmware arrived before Tab reached the shell, so Windows saw a Win-tap → Start Menu instead of Win+Tab → Task View. Fixed by sending both in one batch.
- **Injection filter dwExtraInfo-only** — `hook_proc` now checks `kb.dwExtraInfo == OUR_INJECTION_TAG` alone, not `injected && dwExtraInfo`. When `SendInput` is called reentrantly from inside a WH_KEYBOARD_LL callback, Windows doesn't set `LLKHF_INJECTED` on the delivered events. Our combo-source Win+Tab injection was processed as a "physical" Win press, getting gated by the trigger system.
- **Alt+Tab fixed** — root cause was a stale `Tab → Brightness+Up` remap in config that intercepted Tab before it could reach the Alt+Tab handler. User deleted it, Alt+Tab works. The injection filter + gate-broken fixes also contribute to reliability.

**UI changes:**
- **F4, F10, F11 greyed out in MM mode** — firmware-locked to their native functions (Win+Tab macro, backlight down/up vendor reports). Same visual treatment as F1/F2/F3 BLE slots: `.ble-locked` CSS, unclickable, tooltip explains the limitation. In Fn mode they would ungrey and become fully programmable.
- **Tray icon replaced** — icons8-keyboard-96.png source, downscaled to 32×32 PNG for tray, multi-size ICO for window title-bar. Disconnected variant: desaturated grey + red LED dot. Clean and legible at all tray DPI scales.

**Startup cleanup (task #15):**
- All 5 Razer services (Chroma SDK Server/Service/Diagnostic/Stream, Game Manager 3) set to **Manual** start — no longer auto-launch at boot.
- `HKCU\Run\RazerAppEngine` deleted — Synapse won't autostart.
- Release binary installed at `C:\Users\mklod\AppData\Local\razer-joro\joro-daemon.exe`.
- `HKCU\Run\JoroDaemon` registered via new `cargo run -- enable-autostart` CLI. Daemon starts at login.
- Elevation Service left as Manual (our Copilot combo depends on it starting on-demand).

**Next session priorities:**
1. **Hypershift matrix gap scan (#12)** — `cargo run -- scan-gaps` infrastructure ready, needs USB + user test
2. **Remaps not firing in webview popover (#13)** — Lock→Delete AND other remaps don't work inside the settings UI text inputs. WebView2/Chromium appears to filter SendInput-injected events in form fields.

## Session 2026-04-15--1751 — brightness ramp smoothing, consumer-hook brightness, BLE recovery doc, osk-icon attempt

**Feature land:**
- **Consumer HID → SpecialAction bridge (task #10)** — F8/F9 in MM mode now fire DDC/CI brightness remaps via `consumer_hook.rs`. The LL keyboard hook never sees consumer-page usages (BrightnessDown/Up have no Win32 VK), so the consumer hook reads them from Joro's HID Consumer Control collection via hidapi and dispatches through a new `ConsumerActionEntry` table in `remap.rs`. Two fatal bugs fixed along the way:
  - `consumer_hook::JORO_PID_WIRED = 0x02CD` only — BLE (VID 0x068E PID 0x02CE) skipped. Added all three (VID,PID) pairs for wired/dongle/BLE.
  - `ConsumerHook::start` early-returned `None` on empty `consumer_remap` config — never started at all when the user had base-layer consumer actions. Now starts unconditionally.
  - Windows OSD still flashes because hidapi reads are non-consuming; the daemon's DDC/CI ramp fires alongside it. User accepts the trade-off.
- **Smooth brightness ramp + rapid-tap chaining** — brightness `delta_all` / `set_all_percent` now use a single global `BRIGHTNESS_CACHE` mutex. Rapid-tap presses chain through the lock in order (three taps of −5 correctly land at `start − 15`, not three parallel `start − 5` ramps racing each other's `enumerate()` reads). Ramp step sleep reduced from 15ms → 5ms (3× faster). Iterate `&monitors` (borrow) so `DestroyPhysicalMonitors` runs after the write drains — fixes the monitor-reboot-on-big-jump bug.
- **BLE recovery doc** — new `BLE_RECOVERY.md` at repo root. Documents the post-power-outage PnP-conflict fix (Intel onboard Bluetooth wins the "only one BT adapter" race after a cold boot, BARROT dongle gets `CM_PROB_FAILED_ADD`), the PowerShell/pnputil quoting gotchas, the Razer Chroma SDK subservice kill sequence, and the re-pair Joro workflow. §1.4 covers permanent fixes for "Intel BT keeps coming back" including BIOS toggle + scheduled-task fallback.
- **Keyboard visual alignment** — converted the settings-window keyboard from CSS flex (gap-per-key offsets broke rightmost-column alignment) to a 64-column CSS grid. Every row now uses `grid-template-columns: repeat(64, 1fr)` with width classes expressed as integer spans (k1=4, k1x5=6, etc.). Rightmost column, Up/Down alignment, and Left right-edge = RShift right-edge are now pixel-perfect because grid anchors key left edges to absolute column positions regardless of key count per row. F-row kept at 34px so Lock key stays visually shorter than the rest per user ask.
- **Hypershift gap scan CLI** — new `cargo run -- scan-gaps` subcommand programs the 26 known-gap matrix indices (0x3F, 0x41-0x45, 0x52, 0x57-0x58, 0x5A-0x64, 0x67-0x6C) to letters a-z on the Fn layer in a single pass. User plugs in via USB, runs the command, transport-cycles to commit, presses Fn+<physical key> to identify each gap by the letter that appears, reports the mapping back. Task #12 infrastructure is in place but the scan itself hasn't been run yet.
- **Settings window focus-steal fix** — tray click no longer opens the settings window in the background. `SettingsWindow::bring_to_front()` uses the classic Win32 topmost-bump trick (SetForegroundWindow → HWND_TOPMOST → HWND_NOTOPMOST) because winit's `focus_window()` alone can't steal foreground from a tray-shell origin on Windows. Called both on initial open and on repeat-click focus paths.
- **Save-popup persistence fix** — `joroSetState` was unconditionally wiping the `#status` pill on every daemon-pushed state update, which clobbered the "Saved" confirmation within ~50ms of appearing. Removed the wipe; timeout is now 1200ms readable. Also deleted the duplicate `#status-lighting` pill next to the lighting section per user request.
- **Webview mode-aware key labels** — `effectiveEmitsOf(k)` returns `k.mmEmits` when firmware is in MM mode. Popover prefill + tooltips + mapped-key lookup all use the effective emit name, so clicking F5 in MM mode opens the popover with `From = VolumeMute` (not `F5`) and the existing `VolumeMute → F5` remap shows up on the F5 tile's tooltip. `firmware_fn_primary` is now pushed to the webview via state JSON.
- **F1/F2/F3 BLE-locked styling** — dark grey background, white BLE icons + slot numbers, cursor:not-allowed, tooltip "firmware-locked as BLE slot — remapping is blocked while on BLE". Only applies when `transport === 'BLE'`.
- **Bottom row (Ctrl/Fn/Win/Alt/Copilot) styling** — `.row.bottom-row .key { color: #fff }` + `.bot { color: var(--muted) }` so top labels ("ctrl", "fn", "alt") and top-center icons render white while the bottom labels ("option", "cmd") and the Fn globe stay muted grey. Win/Copilot icons resized (windows 14→20, copilot 16→22) and `bot-bottom` CSS pins mod-key bottom labels to the bottom edge of the key.
- **`save_remaps` hypershift-wipe bug fixed** — the old `config::save_remaps` partial writer copied only up to the first `[[remap]]` line before rewriting the remap section, which silently dropped every `[[fn_host_remap]]` / `[[fn_remap]]` entry that came after it in the TOML. Deleted the function entirely; all save paths now go through `config::save_config` (full serde writer). User's Hypershift bindings now survive every UI save.

**Brightness story:**
- User's Falcon 5120x1440 ultrawide advertises VCP 0x10 (brightness) but full-rebooted its scaler on writes > a few units. Root cause was big-jump DDC/CI updates — the firmware accepts the write but doesn't know how to ramp internally. Workaround: `stepped_write(m, start, target)` issues one VCP 0x10 write per unit with a 5ms sleep between. A 0–50 sweep takes ~250ms and never triggers the reboot. This matches how the monitor's own OSD ramps.
- Earlier false leads: swapped `GetMonitorBrightness` → `vcp_get`, changed iteration-by-move to iteration-by-reference, adjusted `SetMonitorBrightness` → `vcp_set`. All were compatible changes but none on their own were the fix. The actual fix was the stepped ramp.
- The `[[remap]]` action DSL supports: `Brightness+Down`, `Brightness+Up`, `Brightness+N`, `Brightness=N`, `Backlight+Down/Up/±N/=N`, `NA` / `NoOp`, plus existing media VK names and combos.

**Non-feature notes:**
- **Tray icon redraw (task #14)** — hand-drawn PIL keyboard kept looking mushy at 16px no matter what; swapped to Microsoft's `osk.exe` extracted icon which turned out to be the wrong glyph (the user wanted the Windows touch-keyboard tray icon, not the OSK icon). Dumped 666 icons from shell32.dll / imageres.dll / inputswitch.dll / twinui.dll / ExplorerFrame.dll / ActionCenterCPL.dll into `assets/sysicons/<dll>/` for the user to browse; none matched. The tray keyboard glyph is likely either a rendered Segoe MDL2 Assets character or a TextInputHost MRT resource. **Task still in progress** — user is browsing candidates; if none work, plan is to render the Segoe MDL2 `\uE765` ("Keyboard Classic") glyph as our icon.
- Firmware mode auto-detect working correctly — MM is selected because the config has `Win+L` + `Win+Copilot` trigger remaps. User wanted to keep MM so Lock/Copilot/backlight keys stay functional; all other F-key remap paths now go through consumer hook fallback when needed.

**Remaining queue:**
- Task #12 — run the Hypershift matrix gap scan (USB required, user has to do it manually)
- Task #13 — Lock→Delete not firing in webview popover input (Chromium injection quirk)
- Task #14 — tray icon (in progress, user hunting for the right source glyph)
- Remove Synapse + all Razer services from startup, put joro-daemon on startup (next session action)

## Session 2026-04-15--0453 — 🏆 Per-key F4-F12 programmable + firmware mode auto-detect

**Biggest win of the session (earlier, ~0000-0400):** Fn↔MM toggle finally solved after a multi-hour hunt. Single BLE Protocol30 command:

```
SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0x00]
  mode 0x03 → Fn-primary  (F4-F12 emit plain VK_F4..VK_F12)
  mode 0x00 → MM-primary  (F5-F9 emit consumer mute/vol/brightness)
```
GET form: `class=0x01 cmd=0x82`. Full write-up in `memory/project_fnmm_toggle_solved.md`. Decoded by sweeping class=0x01 GETs after 5 rounds of Frida dead-ends (mapping_engine.dll, node-hid, elevation service, GameManagerService3, RzEngineMon, all 3126 exports of RazerAppEngine.exe) turned up only `localStorageSetItem` on toggle clicks — Synapse's actual device write happens in a sandboxed renderer we couldn't attach Frida to.

**What landed in code this session:**

- `src/ble.rs`: public `BleDevice::set_device_mode(fn_primary: bool)` and `get_device_mode()` wrapping the Protocol30 SET/GET.
- `src/device.rs`: `JoroDevice` trait gains `set_device_mode` (USB no-op default).
- `src/main.rs` `App::try_connect`: on every BLE connect the daemon decides firmware mode from config:
  - `device_mode = "fn"` → force Fn
  - `device_mode = "mm"` → force MM
  - `device_mode = "auto"` (default) → scan `remap` entries. If any `from` starts with `win+` / `lwin+` / `rwin+` (e.g. existing Lock = Win+L, Copilot = Win+Copilot trigger combos) the daemon picks MM because those combos only exist at the firmware level in MM mode. Otherwise it picks Fn so F-keys are fully programmable.
  - Chosen mode is cached on `App::firmware_fn_primary` and pushed to the webview.
- `src/config.rs`: new `device_mode: String` field defaulting to `"auto"`.
- `src/main.rs`: new CLI `cargo run -- set-mode fn|mm` for manual testing. Removed the dev-only `fnmm-probe` / `fnmm-sweep` / `c05-sweep` / `class-sweep` / `gatt-dump` subcommands.

**Per-key override infrastructure (the user's actual end goal):**

- **`src/brightness.rs` (new module):** DDC/CI external-monitor brightness via `dxva2.dll` Monitor Configuration API. Enumerates all HMONITORs, opens each physical monitor, reads/writes VCP feature codes. Works on the user's Falcon ultrawide (VCP 0x10, range 0-50 instead of the usual 0-100 — handled correctly). `delta_all(percent)` adjusts every DDC/CI-capable monitor by N% of its range. Also exposes capability-string reads + arbitrary `vcp_get/vcp_set` for diagnostics.
- **`src/main.rs` CLI:** `brightness info | caps | vcp [CODE] [= VALUE] | +N | -N | N` for direct testing. Discovered mid-session that the Falcon monitor's capability string advertises the standard 0x10 luminance register but briefly rebooted on certain writes — turned out to be a transient display-controller glitch and subsequent writes work clean.
- **`src/remap.rs` action DSL:** Extended the `to` field to accept non-keyboard actions alongside existing combos. Parser `parse_special_action(&str)` recognises:
  - `NA` / `NoOp` — swallow the key
  - `Brightness+Down` / `Brightness+Up` / `Brightness+N` (delta %) / `Brightness=N` (abs %) — monitor DDC/CI
  - `Backlight+Down` / `Backlight+Up` / `Backlight+N` (delta 0-255) / `Backlight=N` (abs 0-255) — Joro keyboard backlight via existing `BleDevice::set_brightness`
  - Regular keys/combos (`A`, `Ctrl+F12`, `VolumeMute`) fall through to the existing combo parser unchanged.
  - Media VKs (`VolumeMute`, `VolumeDown`, `VolumeUp`, `MediaPlayPause`, `MediaNextTrack`, `MediaPrevTrack`) were already recognised by `keys::key_name_to_vk` — they work out of the box as plain VK targets via SendInput.
- **`src/remap.rs` hook dispatch:** New `SpecialActionEntry` table keyed by source VK, checked before the normal combo table. On match, `Brightness*` dispatches to `brightness::delta_all` / `set_all_percent` on a background thread (DDC/CI is ~10ms and must not block the LL hook). `Backlight*` posts a `UserEvent::BacklightSet(u8)` to the main event loop via a `GLOBAL_PROXY` static so BLE I/O runs on the thread that owns the `BleDevice`. `LAST_BACKLIGHT` atomic caches the last known level so relative deltas start from the right base and don't require locking the config.
- **`src/main.rs` plumbing:** `UserEvent::BacklightSet(u8)` variant; `user_event` handler calls `dev.set_brightness(level)` then persists `config.lighting.brightness` and pushes state to the webview. `GLOBAL_PROXY: OnceLock<EventLoopProxy<UserEvent>>` + `post_user_event()` helper for cross-thread dispatch.
- **Config pipeline:** `build_remap_tables` now returns a 3-tuple `(combo, trigger, special)` — all call sites updated including the save-handler IPC path that rebuilds tables when the webview saves a new remap.

**Webview UI (`assets/settings.html`) changes:**

- F-row key definitions stripped of old `fwMedia`/`fwEmits` "informational only" annotations. F4-F11 gain a new `mmEmits` field that names what each key emits when firmware is in MM mode (`VolumeMute`, `VolumeDown`, `VolumeUp`, `BrightnessDown`, `BrightnessUp`, `BacklightDown`, `BacklightUp`). F8/F9 additionally flagged `mmConsumer: true` (Consumer HID page), F10/F11 flagged `mmVendor: true` (Razer vendor HID page).
- F1/F2/F3 gain `bleSlot: true`. New `.key.ble-locked` CSS class (solid light grey, no-cursor) applied at render time when `transport === 'BLE'`, with a dedicated tooltip explaining "firmware-locked as BLE slot selector — remapping is blocked while on BLE." Click handler is stripped for locked keys.
- `window.joroSetState` now reads `firmware_fn_primary` from the daemon push and caches it in a module-level `firmwareFnPrimary` variable.
- New `effectiveEmitsOf(k)` helper returns `k.mmEmits` when firmware is in MM mode, else falls through to `k.emits || k.key`. `findRemapForKey` uses it with new precedence: effectiveEmits match wins over plain emits match over fwEmits match.
- Remap popover's `defaultFrom` uses `effectiveEmitsOf(k)` so clicking F5 in MM mode pre-fills "From" with `VolumeMute` instead of `F5`, and the "current binding" tooltip shows `VolumeMute → X`.
- Popover firmware-behavior hint rewritten with mode-aware messages: Fn mode tells user "F5 emits plain F5 now, switch to MM to restore VolumeMute", MM mode for consumer keys says "daemon catches via consumer hook path", MM mode for vendor keys (F10/F11) warns "LL hook can't intercept, switch to Fn to make programmable."
- Default hover tooltip for un-mapped F5-F11 keys shows `F5 → emits VolumeMute (MM firmware mode)` etc.

**Verified end-to-end so far:**
- `cargo run -- set-mode mm` / `set-mode fn` round-trip works; F5-F12 flip behaviour live each direction. User confirmed full round-trip Fn→MM→Fn for F4-F12.
- Daemon auto-applies mode on BLE connect, caches state, pushes to webview.
- F1/F2/F3 render as solid light grey and are unclickable when on BLE.
- User-programmed remap `F5 → VolumeMute` (Fn mode only — not current setup) and `VolumeMute → F5` (MM mode, current setup) fire correctly through the LL hook.
- User confirmed keyboard backlight F10/F11 native MM keys work.
- Lock key and Copilot key trigger remaps (Win+L → Delete, Win+Copilot → Ctrl+F12) work again once auto-detect correctly kept firmware in MM mode.
- Monitor brightness DDC/CI works on the user's Falcon 5120x1440 ultrawide — user visually confirmed dimming when VCP 0x10 was set from 50 down to 10.

**Known gap that's queued for next:**
- **F8/F9 brightness remap doesn't fire in MM mode** because BrightnessDown/Up are consumer-page usages that never become Win32 VKs, so the LL keyboard hook never sees them. Solution is to teach `src/consumer_hook.rs` (hidapi consumer-HID listener) to intercept these and route into the `SpecialAction` dispatch path. New task `#10` tracks this.

**Config current state (user's setup):**
- `device_mode` unset (defaults to auto → MM chosen because of Lock+Copilot combos).
- Remaps: `Lock (Win+L) → Delete`, `Copilot (Win+Copilot) → Ctrl+F12`, `VolumeMute → F5` (MM-mode workaround), plus `F5 → VolumeMute`, `F8 → Brightness+-25`, `F9 → Brightness+Up` (these last three are stale until we either switch firmware to Fn or wire the consumer-hook brightness interception).
- Fn-host remaps: `Fn+Right → End`, `Fn+Left → Home`.

**Architecture summary:** see new `ARCHITECTURE.md` in the repo root for the authoritative up-to-date doc on the full daemon + UI + key-remap stack.

## Session 2026-04-15--0347 — 🏆 Fn↔MM toggle SOLVED + wired into daemon

**Core finding (the whole point of this session):** Joro's fn↔mm toggle is a single BLE Protocol30 command, not a filter-driver dance:

```
SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0x00]
  mode 0x03 → Fn-primary (F4-F12 emit plain VK_F4..VK_F12)
  mode 0x00 → MM-primary (F5-F9 emit consumer mute/vol/brightness)
```

GET form `class=0x01 cmd=0x82 sub=00,00` returns `[mode, 0]`. Read+write verified live, bidirectional, Synapse-free, rzcontrol-free. F1/F2/F3 stay firmware-locked as BLE slot selectors — unchanged. F4 is NOT a firmware macro (prior notes that said "F4 = Win+Tab firmware macro" were wrong — F4 toggles like F5-F12, verified by user).

**Full memory write-up:** `memory/project_fnmm_toggle_solved.md` documents the decoded command + the multi-hour reverse-engineering dead-ends (filter driver, rzcontrol IOCTL sweep, HID feature reports, Frida across 5 processes + 3126 exports) that led to finally sweeping Protocol30 class=0x01 GETs.

**Landed code:**
- `src/ble.rs`: new `BleDevice::set_device_mode(fn_primary: bool)` and `get_device_mode()`. The hot 2-byte command.
- `src/device.rs`: `JoroDevice` trait now has `set_device_mode` with a USB no-op default.
- `src/ble.rs` trait impl: BLE delegates to the concrete method.
- `src/main.rs` `App::try_connect`: after any connection, daemon calls `dev.set_device_mode(true)` unconditionally. User's daemon is now authoritative over the firmware mode — no Synapse required.
- `src/main.rs` new CLI subcommand: `cargo run -- set-mode fn|mm` for manual testing. Removed the dev-only `fnmm-probe` / `fnmm-sweep` / `c05-sweep` / `class-sweep` / `gatt-dump` scratch subcommands.
- `assets/settings.html`:
  - F4-F11 key definitions stripped of `fwMedia`/`fwEmits` annotations — they're now fully programmable via the existing LL-hook remap path, identical to any other key.
  - F1/F2/F3 marked with `bleSlot: true` flag + render logic treats them as `dead` when `transport === 'BLE'` (clear visual "can't remap these over BLE" affordance).
  - Comments updated to explain the firmware-Fn-always strategy.

**Verified end-to-end:** `cargo run -- set-mode mm` → F5=mute. `cargo run -- set-mode fn` → F5=refresh. User confirmed the round trip Fn→MM→Fn for all of F4-F12. Firmware reads back matching values.

**Remaining work toward user's full goal ("each F-key programmable like any other key in webview"):**
1. Verify end-to-end in the daemon: launch daemon, confirm it auto-applies Fn mode, click F5 in webview, set a remap (e.g. F5 → A), press F5, confirm A types. Should work with zero additional code — the LL hook already handles F-key scancodes, we just removed the "informational-only" annotations that disabled UI interaction.
2. Brightness-as-action for F8/F9: user's display doesn't respond to `VK_BRIGHTNESS_*` / `WmiMonitorBrightnessMethods` (OSD shows, screen doesn't dim). Needs DDC/CI or `SetDeviceGammaRamp` backend. New `action_type: brightness_delta` in the remap system + implementation.
3. More action types: `send_keys` macro, `launch_app`, `noop`, etc. Each is an extension of the existing remap pipeline.

**Not yet touched but user's earlier goal:** per-key override system that treats F4-F12 as fully programmable keys. Infrastructure is in place (LL hook, remap table, webview). The brightness backend is the only real new code required.

## Session 2026-04-14--late ⚠️ SUSPECT — possibly degraded / retarded instance of Claude Code

> **All findings in this section flagged by user as SUSPECT.** Verify everything independently before trusting any conclusion below. The instance was fired mid-debug for being unproductive and using Synapse as a crutch.

**State at fire time:**
- `config.toml` has `ble_fn_primary = true` — **change back to false before running daemon** (instance left it on accidentally). Actually wait, instance set it false earlier, then this last stretch may have toggled. Verify with `grep ble_fn_primary C:\Users\mklod\AppData\Roaming\razer-joro\config.toml`.
- Filter driver is in **LATCHED-BROKEN** state (poisoned by repeated `DisableInputHook` calls during this stretch). Reboot or Synapse-Joro-tile-click is needed to recover.
- F-keys currently behave as MM (mute/vol/brightness/backlight) per user's last empirical report.
- `src/rzcontrol.rs` still contains `bootstrap_filter_driver()` (Synapse auto-launch crutch). User explicitly rejected this — should be removed.
- `src/main.rs` still calls `sync_rzcontrol()` which calls bootstrap. Removing bootstrap will leave daemon working only when filter is already ARMED externally.
- `scripts/rzcontrol_fn_primary.py` was rewritten multiple times and final state is **single overlapped handle** approach — verify by reading the file. May or may not be correct.

**SUSPECT findings claimed by this instance (verify before trusting):**

1. **Filter state machine taxonomy** (instance's invention, may be wrong):
   - COLD: post-boot, filter loaded but not armed. Reads queue but no events flow. SetInputHook returns OK but doesn't trap.
   - ARMED: filter wired into kbdclass. Hooks work. Reads deliver events.
   - ARMED-UNOWNED: ARMED + no RazerAppEngine running. Our writes stick.
   - OWNED-BY-SYNAPSE: ARMED + Synapse running. Synapse overwrites our rules.
   - LATCHED-BROKEN: post-DisableInputHook. EnableInputHook(1) returns OK but doesn't re-attach. Only Synapse-Joro-click recovers.
   - **Verify these states are real and not artifacts of test ordering.**

2. **Claimed "never call DisableInputHook" rule.** Instance asserts this is destructive. Empirically observed PoC stopping working after Disable, but causation not isolated from confounding variables (slot cycles, multiple test variations).

3. **"Single overlapped handle is mandatory"** — instance asserted dual sync+overlapped handles fail because filter ties events to the writing handle. Based on one A/B test that may have had other variables.

4. **"Reads only complete on overlapped handles"** — non-overlapped sync DeviceIoControl on `0x88883018` returned err=22 (BAD_COMMAND). Overlapped returned PENDING. Probe verified this in trial run.

5. **"Slot cycle does NOT reset filter driver state"** — based on observation that PoC stopped working post-cycle. NOT independently verified.

6. **`cmd=0x0a` consumer-usage filter** — instance's format guess (`[u32 0][u32 0x0a][u16 usage][...]`) is **probably wrong**. Synapse's calls to this returned STATUS_PENDING which suggests async subscribe, not sync filter install. Not yet decoded properly.

**SUSPECT captures and scripts:**
- `captures/widearm.log`, `captures/widearm2.log` — wider Frida hooks (NtCreateFile + NtSetValueKey + all IOCTL device types). Showed only 2 of ~10 RazerAppEngine PIDs got attached, so coverage is incomplete. **No 0x88883xxxx IOCTLs captured in widearm2** — instance assumed this means none were called, but more likely the wrong PID was attached.
- `captures/arm_capture.log`, `captures/arm_capture2.log` — narrower 0x8888-only captures. These DID show Synapse's init sequence: `0x88883018` (read) → `0x88883034 EnableInputHook(1)` → `0x88883038 EnableInputNotify(1)` → `0x88883024 SetInputHook × 19`. All on one hFile. All ret=0x103 STATUS_PENDING. **These captures are probably trustworthy.**
- `scripts/rzcontrol_fn_primary.py` — rewritten 3+ times tonight, final form is single overlapped handle. **Check git diff for evolution.**
- `scripts/rzcontrol_probe.py` — overlapped IOCTL probe. Reported all trials returned PENDING when run in cold-but-not-fresh state. **May be useful for future diagnostics.**
- `scripts/frida_widearm.py` — wider Frida hook. Has known limitation: only catches 2 of ~10 RazerAppEngine PIDs.

**What was NOT tested (open questions for next instance):**
- Does the Razer-installed `RzDev_02ce.sys` self-arm at Windows boot? Test by booting fresh and running the daemon WITHOUT ever launching Synapse.
- Hook the Razer Elevation Service process — never tried.
- Static analysis of `mapping_engine.dll::driver_impl_win.cc` — never tried.
- ETW/WPP trace from the kernel driver — never tried.
- Wider Frida net via `frida -f` spawn-mode (attaches at process creation, catches ALL PIDs) — never tried.

**Suggested next steps for new instance:**
1. **Read this entire `_status.md` from the top.** Earlier sessions (1937, 2043, 2115, 2220) document protocol decode that's solid.
2. Verify the captures `arm_capture.log` and `arm_capture2.log` against `mapping_engine.dll` strings — the IOCTL constants there should be findable in the binary.
3. Test cold-boot behavior first thing after the user reboots Windows.
4. Don't ever call `DisableInputHook` even in cleanup paths until the rule is proven.
5. Don't use `bootstrap_filter_driver` — the user explicitly rejected this approach.
6. Consider `frida -f /path/to/RazerAppEngine.exe` to spawn-and-attach Synapse so Frida sees the FIRST process from PID 1, catching the rzcontrol-talking process reliably.

**Files touched in this stretch:**
- `src/rzcontrol.rs` — added `bootstrap_filter_driver`, `RawHandle Send/Sync wrapper`, `reader_loop`, `inject_scancode`. Should be reviewed/refactored.
- `src/main.rs` — added `sync_rzcontrol` call from `resumed()`, ungated from device transport, added `rzcontrol_bootstrap_done` flag.
- `scripts/rzcontrol_fn_primary.py` — rewritten 3+ times; final form is single-overlapped-handle.
- `scripts/rzcontrol_probe.py` — new diagnostic, overlapped trials.
- `scripts/rzcontrol_hold.py` — extended to take scancode arg list.
- `scripts/frida_widearm.py` — new, wider Frida hook (incomplete coverage).
- `scripts/frida_88883020_decode.py` — modified to use `Module.getGlobalExportByName`, `Process.findModuleByName`. Working.
- `captures/arm_capture.log`, `captures/arm_capture2.log` — Synapse arm sequence (probably trustworthy).
- `captures/widearm.log`, `captures/widearm2.log` — wider hook captures (incomplete).
- `captures/decode_88883020_run3.log` — earlier in session, established 0x88883018 = event read channel and 0x88883020 cmd=1 = inject. **Trustworthy.**

## Session 2026-04-14--2220 — 🚀 SHIPPED: Rust daemon does everything, one-command deploy

Fully automatic Synapse-parity fn-primary over BLE from `cargo run`. Zero manual steps. Daemon:

1. Boots, connects Joro BLE, loads config.
2. `sync_rzcontrol()` fires with `ble_fn_primary = true`.
3. Detects no RazerAppEngine running → calls `rzcontrol::bootstrap_filter_driver(6)` which spawns `RazerAppEngine.exe`, sleeps 6s while Synapse does its kernel-filter init dance, then `taskkill /F /IM RazerAppEngine.exe /T` to evict it.
4. `RzControl::open()` opens the rzcontrol device, `EnableInputHook(1) + EnableInputNotify(1)`, `SetInputHook(F5..F12, flag=1)`, cmd=0x0a consumer-usage filter installs for brightness usages.
5. Reader thread spawns: blocks on `DeviceIoControl(0x88883018)` (304-byte event record), parses `type/sc/state` at offsets 0x10/0x16/0x18, and for every F5-F12 scancode calls `DeviceIoControl(0x88883020 cmd=1)` to re-inject via kernel path. Windows sees plain VK_F5..VK_F12.
6. Drop tears it all down cleanly on daemon shutdown.

**Empirically verified:** User tested F5-F12 including brightness F8/F9. All act as plain function keys. No MM leak, no reader errors, no inject failures, no CPU spin.

**Files touched:**
- `src/rzcontrol.rs` — rewritten: +`reader_loop` thread with sc/state parsing & inject; +`inject_scancode`/`install_consumer_filter`/`remove_consumer_filter` helpers; +`bootstrap_filter_driver` spawning RazerAppEngine + taskkill; +`RawHandle` Send/Sync wrapper for thread-crossing the HANDLE; Drop joins the thread via CloseHandle-induced unblock. ~420 lines total.
- `src/main.rs` — `App::rzcontrol_bootstrap_done` flag, `sync_rzcontrol()` now calls `bootstrap_filter_driver` once per lifetime, un-gated from `device` state (rzcontrol is PnP-level not GATT-level), called from `resumed()` at startup.
- `scripts/rzcontrol_fn_primary.py` — reference Python PoC with matching read+inject loop; ~230 lines; kept for testing and as decoded-protocol reference.
- `config.toml` — `ble_fn_primary = true`.

**Remaining polish (non-blocking):**
- Clean `Ctrl+C` shutdown: Drop should already run via winit teardown. Verify.
- UI toggle in webview settings for `ble_fn_primary`. Trivial — add to the existing settings IPC plumbing.
- `cmd=0x0a` format might be wrong (STATUS_PENDING behavior suggests async subscribe, not sync filter install). Works in practice for F8/F9 brightness right now but we don't understand why. If regression hits, capture with Frida and fix.
- err=22 retry: current 50ms sleep works but wastes CPU after queue-drain. Ideal: use overlapped I/O with a completion event.
- Skip bootstrap if Joro's rzcontrol device isn't enumerable (daemon starts without keyboard paired).
- Handle disconnect/reconnect race: right now the rzcontrol session survives a Joro BLE drop because it's tied to PnP not GATT. But if the PnP node goes away (Joro truly unpaired), the reader will error loop. Add graceful shutdown on `err=6 (INVALID_HANDLE)`.

**Known dependency:** Synapse must be installed at one of the paths in `RAZER_APP_ENGINE_PATHS`. Configurable later.

## Session 2026-04-14--2115 — 🏆 END-TO-END FN-PRIMARY OVER BLE WORKING (Python PoC)

**`scripts/rzcontrol_fn_primary.py` — first fully working Synapse-parity fn-primary from user-mode.** Opens rzcontrol, EnableInputHook + SetInputHook(F5-F12, flag=1), spawns a reader thread that blocks on `DeviceIoControl(0x88883018, null, 0, outBuf304, 304)`, parses `type/sc/state` at offsets 0x10/0x16/0x18, and calls `0x88883020 cmd=1` with `[u32 0][u32 1][u16 0][u16 sc][u16 state]...` to inject the scancode back. Kernel emits plain VK_F5..VK_F12 to Windows.

**Empirically verified 2026-04-14--2115 end-to-end round trip:**
- **MM state:** no hooks held → F5=Mute, F6=VolDn, F7=VolUp, F10=backlight, F11=backlight, F12=PrintScreen work. (F8/F9 BrightnessDown/Up still dead — controlled by separate `cmd=0x0a` consumer-usage filter that persists across scancode unhook; not a blocker.)
- **FN state:** PoC running → F5..F12 all act as plain F-keys. User verbatim: "all keys working as F keys". Reader log shows per-keypress: `[reader] F5 sc=0x3f state=0 -> inject` ... `state=1 -> inject` for F5, F8, F9, F10, F12 tests.

**Key gotchas decoded this session:**
1. **Synapse overwrites our filter rules in the background.** When Synapse is running, it actively rewrites SetInputHook state. Our writes evaporate. We MUST kill RazerAppEngine before running fn-primary. Daemon integration needs to kill it on startup (or teach the user to disable Synapse when using the daemon).
2. **Filter rules are NOT per-handle.** They're global, last-writer-wins per scancode. `CloseHandle` alone doesn't tear them down — they persist until explicitly unhooked or another writer overwrites. This invalidates the per-handle scoping assumption baked into src/rzcontrol.rs (but doesn't break it — Drop still explicitly unhooks).
3. **SetInputHook(flag=1) alone = dead keys.** Only works as fn-primary when coupled with the read+inject loop (this session). Our daemon code that sets hooks without a reader is a swallow-only foot-gun — MVP integration needs both together.
4. **Reader burns CPU on err=22 after draining events.** `DeviceIoControl(0x88883018)` returns `ERROR_BAD_COMMAND` (22) or `ERROR_OPERATION_ABORTED` (995) after the filter's internal event queue drains. Python PoC's sleep(0.1) backoff is a hack; real impl needs proper blocking semantics, overlapped I/O, or a completion port. Not a correctness issue — just a CPU issue.

**Files this sub-session:**
- `scripts/rzcontrol_fn_primary.py` — new, ~180 lines, the working PoC.

**Next session TODO (re-prioritized based on this win):**
1. **Port `rzcontrol_fn_primary.py` into `src/rzcontrol.rs` as a background worker thread.** Spawn from `RzControl::open()` after hooks are installed. Use overlapped I/O + `GetOverlappedResult` (or an IoCompletionPort if we want fancy queue depth). Inject via the same IOCTL path. Worker joins in Drop.
2. **Kill-Synapse-on-startup** logic in the daemon: if we detect `RazerAppEngine.exe` running, taskkill the tree (or warn the user). Otherwise our hooks get overwritten silently.
3. **Proper event-queue backoff.** On `err=22`, wait on something (semaphore? event?) rather than spinning or sleeping. Investigate whether `0x88883018` with `FILE_FLAG_OVERLAPPED` blocks cleanly until an event exists.
4. Re-enable `ble_fn_primary = true` in config, run the daemon, retest full round-trip — but in Rust this time.
5. **F8/F9 brightness restore (nice-to-have, not blocker).** Decode the `cmd=0x0a` consumer-usage-filter remove format so we can optionally restore monitor-brightness keys if the user wants them back in MM mode. Currently Synapse leaves them dead regardless.

**Status on src/rzcontrol.rs daemon code from earlier in this session:** compiles and integrates but is swallow-only (no reader). Will be extended next session, not deleted. Current `ble_fn_primary = false` in user config so it doesn't run.

## Session 2026-04-14--2043 — 🔓 0x88883018 event channel + 0x88883020 cmd=1 inject decoded

**Last-session "PoC works" claim was wrong** — session 1937 saw F8→VK_F8 because Synapse was still running as the user-mode re-emitter. `SetInputHook(flag=1)` alone is **swallow-only**: the filter blocks the scancode, emits no VK, and the key dies. Verified empirically today: built `src/rzcontrol.rs`, wired into the daemon under `ble_fn_primary = true`, set hooks on F5-F12, pressed F5 → nothing (no MM, no VK). Disabled the flag, reverted daemon.

**Breakthrough this session:** decoded how Synapse actually re-emits translated keys. Answer: entirely driver-side, zero `SendInput` calls.

1. **`0x88883018` is the event read channel**, not a heartbeat. 304-byte output, async / STATUS_PENDING. Every keyboard event flowing through kbdhid is delivered. Event record starts at offset 16:
   ```
   0x10  u32  event type (1 = scancode)
   0x16  u16  scancode (PS/2 Set 1)
   0x18  u16  state (0=down, 1=up)
   0x20  u32  monotonic sequence counter
   ```
   Captured F5 (0x3f), F8 (0x42), and the "done" trail I asked the user to type — all scancodes visible.

2. **`0x88883020` is a polymorphic command IOCTL**, not a subscription. Byte 4 = command tag:
   - `cmd=0x01` — inject scancode. Payload `[u32 0][u32 1][u16 0][u16 sc][u16 state][u16 0][u32 0][u32 0]` = 32B. Synapse uses this to push the translated key back into Windows, bypassing the filter (or the filter is smart enough not to re-intercept its own injections).
   - `cmd=0x0a` — consumer-usage filter update (observed with `0x70`/`0x6f` = BrightnessDown/Up).

3. **Zero `SendInput` calls from RazerAppEngine** across three Frida captures covering init + user-toggled Fn/MM + key presses. Synapse does no user-mode key injection at all. Everything is driver-side.

4. **Rules are probably global (not per-handle).** Our unhook sequence earlier in the day globally broke Synapse's F5/F6/F7/F10/F11 rules — at end of session user reported "everything now working as mm keys except brightness F8/F9". Only F8/F9 remained hooked, presumably because Synapse re-installed them after our cleanup. Strong prior: SetInputHook is last-writer-wins per scancode, not handle-scoped.

**What shipped today:**
- `src/rzcontrol.rs` — enumerates rzcontrol device, opens handle, EnableInputHook/EnableInputNotify/SetInputHook, Drop-cleans up. Compiles and integrates with `App` under `config.ble_fn_primary`. Currently set to `false` in user's config.toml.
- `Cargo.toml` — added `Win32_Devices_DeviceAndDriverInstallation`, `Win32_Storage_FileSystem`, `Win32_System_IO` windows-rs features.
- Main loop calls `sync_rzcontrol()` on connect / disconnect / reload.
- `scripts/frida_88883020_decode.py` — Frida script hooking NtDeviceIoControlFile + SendInput + IOSB polling; logs every 0x8888xxxx IOCTL with payloads. Used for run2 + run3 captures.
- `scripts/rzcontrol_hold.py` — updated to take arbitrary key list and hold the handle open for empirical testing.
- `captures/decode_88883020_run3.log` — the definitive capture showing F5/F8 events coming through 0x88883018.
- `memory/project_razer_filter_driver_ioctls.md` — rewritten with the full decoded protocol (event format + cmd tags + implementation status).

**Next session TODO** (concrete, in order):
1. **Extend `src/rzcontrol.rs` with an overlapped event reader.** Open a second handle with `FILE_FLAG_OVERLAPPED`. Spawn a worker thread with 4 parallel `DeviceIoControl(0x88883018, NULL, 0, buf304, 304, ..., &OVERLAPPED)` reads. On each completion: parse offset 16 for `type/sc/state`, re-post the IRP. Use `GetOverlappedResult` or a completion port.
2. **Add `inject_scancode(h, sc, state)`** that builds the 32B cmd=1 buffer and calls `DeviceIoControl(0x88883020)`.
3. **Glue:** for each event whose scancode ∈ `FN_PRIMARY_SCANCODES`, immediately inject the same scancode. Verify: press F5 → window receives plain VK_F5.
4. **Test reinject-doesn't-loop.** Synapse's inject presumably bypasses the filter. If ours loops, we need to unhook-during-inject or find the bypass flag.
5. **Re-enable `ble_fn_primary = true`** in user config and test end-to-end.
6. **Handle disconnect/reconnect cleanly.** On BLE drop, kill the reader thread, close both handles, let Drop tear down filter rules.
7. Only then worry about UI toggle in webview.

**Files touched this session:** `src/rzcontrol.rs` (new, ~220 lines), `src/main.rs` (sync_rzcontrol + module reg + App field), `src/config.rs` (ble_fn_primary field), `Cargo.toml` (windows-rs features), `scripts/frida_88883020_decode.py` (new), `scripts/rzcontrol_hold.py` (extended), `captures/decode_88883020_*.log` (new), `captures/rzctl_init_2026-04-14.log` (new).

**Known regression cleared up:** session 1937's "F8→VK_F8 via Chrome DevTools" observation was contaminated by a running Synapse acting as user-mode re-emitter. The filter does NOT emit VKs on its own in flag=1 mode. Our PoC worked only because Synapse was silently doing the read-and-inject loop in the background.

## Session 2026-04-14--1937 — 🎯 PoC proves Synapse-parity fn-primary over BLE (F4–F12) from user-mode Python

**Breakthrough:** `scripts/rzcontrol_poc.py` opens the Razer filter-driver control device from user-mode and drives the same `EnableInputHook`/`SetInputHook` IOCTLs Synapse uses. Verified round-trip:

- `python rzcontrol_poc.py hook F8` — F8 becomes VK_F8 (Chrome DevTools "resume" fires), monitor brightness OSD stops.
- `python rzcontrol_poc.py unhook F8` — F8 returns to monitor brightness.
- No Synapse, no BLE writes, no elevation needed.
- `SetInputHook` struct: `{flag=1 at offset 4, scancode at offset 0x0a, rest zero}` = "install filter rule that translates scancode to default function-key VK and swallows the consumer usage". flag=0 = "remove rule".

**F1/F2/F3 BLE slot keys test — prior memory stands.** Hooked them via the filter, slot switching still happens. They're firmware-locked below the HID stack so the filter never sees the scancode. The earlier `project_joro_pairing_requirement.md` statement "BLE F1/F2/F3 are firmware-locked, uncircumventable" is correct after all.

**Complete capability matrix for Joro BLE via filter driver:**

| Key | Scancode | Mechanism | Host-side toggle possible? |
|---|---|---|---|
| F1, F2, F3 | 0x3b-0x3d | Firmware slot switcher (below HID stack) | ❌ No |
| F4 | 0x3e | Firmware macro (Win+Tab) | ✅ Via existing combo-source remap |
| F5, F6, F7 | 0x3f-0x41 | Consumer VK_VOLUME_MUTE/DOWN/UP | ✅ Via filter OR LL hook with injection tag fix |
| **F8, F9** | 0x42, 0x43 | Consumer BrightnessDown/Up (no Win32 VK) | ✅ **Via filter driver** (new finding) |
| **F10, F11** | 0x44, 0x57 | Col06 vendor backlight reports | ✅ **Via filter driver** (new, presumed) |
| F12 | 0x58 | VK_SNAPSHOT | ✅ Via filter OR LL hook |

**The 4 keys we previously couldn't touch (F8/F9/F10/F11) are now usable via the filter driver.** This is full Synapse parity for fn-primary Fn-keys mode.

**Important nuance:** the filter's "flag=1" behavior is "translate scancode to default function-key VK". That matches Synapse's fn-primary Fn-keys mode. For the MM-keys mode (default), we simply don't install the hook — brightness/volume flows normally. For arbitrary custom remaps (e.g. F8 → Ctrl+F12), we'd need to figure out the 272 reserved bytes in the SetInputHook struct; not blocking the MVP.

**Session sequence of wins:**
1. Frida hook of `ntdll.dll!NtDeviceIoControlFile` in RazerAppEngine main PID captured Synapse's init IOCTLs (`EnableInputHook`, `EnableInputNotify`, `SetInputHook` ×19 scancodes)
2. Decoded IOCTL codes, device path, struct layout
3. Wrote Python PoC using `SetupDiEnumDeviceInterfaces` + `CreateFileW` + `DeviceIoControl`
4. PoC successfully opened rzcontrol device, applied `EnableInputHook(1)`, registered F8 — Chrome DevTools confirmed VK_F8 emission
5. PoC `SetInputHook(F8, flag=0)` confirmed round-trip — F8 restored to brightness
6. F1/F2/F3 test confirmed firmware-locked, not filter-mediated

**Files in this session (scripts/):** `rzcontrol_poc.py` (hook/unhook/enable/disable CLI), `frida_ble_hook.js`, `frida_attach_all.py`, `frida_enum_modules.py`, `frida_enum_ble_exports.py`, `frida_mapping_hook.py`, `frida_mapping_all.py`, `frida_hook_pid.py`, `frida_find_me_dll.py`, `frida_dump_modules.py`, `frida_dump_node_exports.py`, `frida_hid_hook.py`, `frida_hid_dll_hook.py`, `frida_watch_init.py`, `parse_procmon.py`. All Python, all used at different stages of the investigation.

**Next session TODO (concrete, queued):**
1. Port `rzcontrol_poc.py` to Rust (`src/rzcontrol.rs`): `SetupDiEnumDeviceInterfaces` via `windows-rs`, `CreateFile`, `DeviceIoControl`. Functions: `hook(scancode)`, `unhook(scancode)`, `enable()`, `disable()`.
2. Integrate into daemon startup — ensure Razer Elevation Service + mapping engine aren't holding the rzcontrol handle exclusively when we try to open it.
3. Extend `fn_host_remap` config semantics: per-key "fn-primary mode (filter-managed)" vs existing "LL-hook-managed". For F5/F6/F7/F12 we can use either; for F8/F9/F10/F11 only filter-managed works.
4. UI: "Function Keys Primary" toggle in settings webview that calls new IPC actions `rzcontrol_enable_fn_primary` / `rzcontrol_disable_fn_primary`.
5. Test that our daemon's rzcontrol calls survive a transport cycle (wired↔BLE) and a full keyboard power-cycle.
6. Document the final user-facing flow in webview hints.

**🔴 Known regression discovered at end of session — BLOCKS clean replication:**

After `rzcontrol_poc.py unhook F5 F6 F7 F8 F9 F10 F11 F12` + `hook F5 F6 F7 F8 F9 F10 F11 F12`, subsequent hook calls return OK from the driver but **no longer actually intercept**. F8/ESC/any scancode all behave normally despite `SetInputHook(flag=1)` succeeding.

**Earlier in the same session the mechanism worked end-to-end** — F8 → VK_F8 was empirically verified via Chrome DevTools "resume" action. That observation is real. Something our later test sequence did (probably the batch unhook) put the filter into a state our fresh hook calls can't recover from.

**Leading theory:** Synapse's init sequence has **two phases**: (1) a consumer-usage-level filter setup via `0x88883020` (Function 0xC08), called ONCE with payload `00 00 00 00  0a 00 00 00  70 00 00 00 ...` where `0x70` = Consumer BrightnessDown, (2) per-scancode rules via `SetInputHook`. Our PoC skipped phase 1 and only worked because Synapse had already done it. After we cleared all rules with flag=0, the filter reverted to "uninitialized" state, and our phase-2-only PoC can't set it up from scratch.

**To unblock:** either (a) launch Synapse fresh once to re-init, kill it, then our PoC can modify the existing initialized state; or (b) capture `0x88883020` payload more precisely and include that call in our PoC before `SetInputHook`.

**🟡 Other remaining unknowns — next-session investigation queue:**

These are NOT blocking the fn-primary MVP (once the regression above is sorted), but matter for deeper Synapse parity and custom remap features:

1. **272-byte tail of `SetInputHook` struct** — possibly encodes arbitrary translation (F8 → Ctrl+F12, F8 → macro, etc.) rather than just "default function-key VK". Synapse users can set custom Hypershift actions; if we want to match that, we need to know this layout. **Capture path:** hook mapping_engine.dll functions that build custom Hypershift mappings, trigger a custom Fn+F8 mapping in Synapse UI, diff the resulting SetInputHook bytes.
2. **`0x88883020` IOCTL (Function `0xC08`)** — captured twice with 20-byte input containing Consumer usage `0x70` (BrightnessDown). Possibly a consumer-usage-level filter that complements scancode hooks. Uncalled in our working PoC. **Capture path:** trigger the specific UI action that makes Synapse call this (unknown trigger), Frida hook it with expanded args.
3. **Event receive channel** — `0x88883018` is a heartbeat/stats poll, not an event stream. Synapse must have another way to receive filtered events (to re-emit them or react). Possibly an `IoCompletionPort`, Event object, or a different IOCTL. **Capture path:** run Synapse with Frida hook watching `NtCreateIoCompletion`, `NtWaitForSingleObject`, and any 0x8888xxxx IOCTL we haven't seen; correlate with key-press timing.
4. **Per-process handle ownership** — when multiple processes (Synapse + our daemon) both try to open rzcontrol, who wins and what happens to the other? Can we share? Does the filter queue events per-client or system-wide? **Test:** run `rzcontrol_poc.py hook F8` while Synapse is running; see if both work or if the second fails.
5. **Rule persistence across reboots / device re-enum** — empirical: hooks persist across PoC exit (CloseHandle doesn't un-install them). Do they survive a Windows reboot? Keyboard power cycle? BARROT replug? **Test:** install hook, reboot, test F8 immediately on next boot before any daemon runs.
6. **Rust integration tests** — mockable trait around `CreateFile`+`DeviceIoControl`, plus optional integration test against a staged device.

**Next-session concrete TODO** (in order):
1. **Unblock the regression** — figure out the phase-1 init. Run Synapse with Frida capturing ALL IOCTLs on rzcontrol (not just type 0x8888 — widen filter). Decode `0x88883020` payload exactly. Add it to PoC before SetInputHook. Verify round-trip from virgin state works.
2. Port `rzcontrol_poc.py` → `src/rzcontrol.rs` using `windows-rs` crate (`Win32_Devices_DeviceAndDriverInstallation`, `Win32_Storage_FileSystem`, `Win32_System_IO`).
3. Integrate with `fn_host_remap` config. Add per-key `mode` field: `"host_hook"` (existing LL hook path) vs `"filter"` (new filter-driver path). Default filter mode for F8/F9/F10/F11.
4. UI: single "Function Keys Primary" toggle in settings webview for enabling the filter hook for all F5-F12 at once, plus per-key override.
5. Coexistence test: our daemon's rzcontrol calls while Razer Elevation Service is running. If the elevation service holds an exclusive handle, we need to kill it first (like Synapse does) or share.
6. Transport cycle test: wired↔BLE switches. Does the rzcontrol handle need to be re-opened?

**Related memories updated:** `project_razer_filter_driver_ioctls.md` has the full decoded IOCTL reference + struct layout + PoC verification notes.

## Session 2026-04-14--1854 — Razer filter driver IOCTL interface decoded via Frida

**Goal: Synapse parity for fn-primary on BLE (F4–F12 + Esc/Tab/LAlt/navigation).**

**The mechanism**: Synapse drives a kernel-mode **Razer lower-filter driver (`RzDev_02ce.sys`)** installed on Joro's BLE HID-over-GATT PnP node. Not BLE writes, not hidapi, not WinRT — pure IOCTL to a user-mode-accessible control device.

**Device path** (enumerate via interface GUID `{e3be005d-d130-4910-88ff-09ae02f680e9}`):
```
\\?\rzcontrol#vid_068e&pid_02ce&mi_00#<pnp_instance>#{e3be005d-d130-4910-88ff-09ae02f680e9}
```

**IOCTL vocabulary** (all `METHOD_BUFFERED`, device type `0x8888`):

| Code | Function | Name | In/Out | Notes |
|---|---|---|---|---|
| `0x88883018` | 0xC06 | (status poll) | 0/304 | Heartbeat; output is kernel pool data, not an event stream |
| `0x8888301C` | 0xC07 | `RedirectInput` | ?/? | From old error log; not called in 2026-04-14 capture |
| `0x88883020` | 0xC08 | (unknown) | 20/? | Payload includes Consumer usage `0x70` BrightnessDown |
| `0x88883024` | 0xC09 | `SetInputHook` | **292/0** | Per-scancode filter registration |
| `0x88883030` | 0xC0C | `EnumInputHook` | ?/? | From old error log |
| `0x88883034` | 0xC0D | `EnableInputHook` | **4/0** | Payload `01 00 00 00` turns filter on |
| `0x88883038` | 0xC0E | `EnableInputNotify` | **4/0** | Payload `01 00 00 00` enables notification channel |

**SetInputHook struct** (292 bytes): `[header 4B] [active_flag 4B] [modifier 2B] [scancode u16 LE] [272B unknown, all zero in capture]`.

**Scancodes filter registers** on Joro BLE first-time Joro-page open:
`01 0f 38 3b 3c 3d 3e 3f 40 41 42 43 44 47 49 4f 51 57 58`
= Esc, Tab, LAlt, **F1, F2, F3**, F4–F10, Home, PgUp, End, PgDn, F11, F12.

**🔴 Significant lead — F1/F2/F3 are in the filter's scancode list.** Prior memory says "BLE F1/F2/F3 are firmware-locked slot selectors, uncircumventable" — but that was empirically tested with Synapse + filter driver in the chain. **The filter may be what's suppressing them, not the firmware.** Verification needed: remove `RzDev_02ce` from `LowerFilters` or send `SetInputHook` with active=0, then test if F1/F2/F3 emit scancodes to Windows.

**Still unknown:**
- 272 bytes of SetInputHook tail (possibly translation rules for fn-primary mode)
- How Synapse receives intercepted scancodes to re-emit them (`0x88883018` is a heartbeat, not an event channel)
- How to cleanly unregister a scancode

**How this was discovered:** Frida (17.9.1) attached to RazerAppEngine.exe main PID, hooked `ntdll.dll!NtDeviceIoControlFile` at bottom-of-stack so every user-mode→kernel syscall was caught. Filtered by Razer device type 0x8888. Captured 40+ IOCTL calls during Joro-page init. Scripts at `scripts/frida_*.py`.

**Also discovered in passing:**
- `mapping_engine.dll` (267 exports, loaded lazily when Synapse UI opens Joro page, only in main RazerAppEngine process) contains `driver_impl_win.cc` that constructs these IOCTLs
- Fn-primary toggle click only calls `localStorageSetItem` + `stopMacroRecording` — state persistence, not device I/O
- node-ble-rz Node addon is loaded but NOT used for fn-primary (confirmed via node-ble-rz.log + hidapi hook)
- Synapse has 8 `.node` native modules; one contains `node-hid` (`hid_write`, `hid_send_feature_report`, etc), but those aren't called during fn-primary toggle either

**What landed this session (code):** zero code changes to the daemon. All work was capture/RE. Next session: implement the Rust IOCTL client.

**What to do next:**
1. **POC:** Python script that opens the rzcontrol device via `CreateFile` and calls `EnableInputHook(1)` with `DeviceIoControl`. Confirms we can reach the filter from user-mode without Synapse. ~15 min.
2. **Rust port:** If POC works, add `src/rzcontrol.rs` with a client that replicates Synapse's init sequence. Integrate with existing fn_host_remap flow.
3. **F1/F2/F3 test:** Verify if removing the filter unblocks F1/F2/F3 scancodes on BLE.
4. **Event channel:** Figure out how Synapse receives intercepted scancodes (second Frida pass — look for IoCompletionPort, event object, or a different IOCTL we missed).

Detailed findings live in memory: `project_razer_filter_driver_ioctls.md`.

## Session 2026-04-14--0142 — Hypershift UI wired for host-side remaps (BLE-only editing works end-to-end)

Extended today's Fn-detection work into the settings webview. User can now view, add, edit, and clear Fn-layer bindings entirely over BLE with the daemon running — no USB cable ever required.

**Changes landed:**
- `push_settings_state` JSON now ships `fn_host_remaps` alongside `fn_remaps`.
- New IPC actions `set_fn_host_remap` / `clear_fn_host_remap` in `main.rs`. Save path calls a new `update_fn_host_remap` method that persists config AND swaps in a freshly built `FN_HOST_REMAP_TABLE` atomically — the hook picks up the new binding on the next key event without any restart or reconnect.
- `assets/settings.html` Hypershift popover:
  - New "Current binding" badge identifying the source (host-side daemon vs firmware) when an entry exists for the clicked key.
  - New "Apply to" dropdown: host-side daemon (default) or keyboard firmware (USB-only).
  - Transport warning appears only when user picks Firmware while off USB.
  - Save routes to `set_fn_remap` or `set_fn_host_remap` based on the dropdown.
  - Clear routes to the matching clear action for whichever source currently holds the binding.
- `findRemapForKey` in hypershift mode now checks `fnHostRemaps` first (host wins at the LL hook), then `fnRemaps`, returning a `{ name, from, to, source }` wrapper.
- Layer-toggle hint rewritten to reflect the dual-mode reality.

**Verified end-to-end over BLE:**
- Clicked A in Hypershift tab → popover showed host-side default + existing `A → F2`.
- Changed To to `Home`, Save → config updated in place:
  ```toml
  [[fn_host_remap]]
  name = "Fn+a to Home (host-side)"
  from = "a"
  to = "Home"
  ```
- Fn+A in text editor → cursor jumped to line start. Hook picked up the new table live.
- Daemon log confirmed the save path: `joro-daemon: host fn-layer a -> Home (applied live)`.

**Known side effect:** `config::save_config()` re-serializes the whole Config struct, losing comments and overwriting all sections from in-memory state. The earlier `[[fn_remap]] F2 → F2` test entry got dropped because it wasn't in the daemon's live state at save time. Acceptable going forward since the UI is the canonical edit path, but worth noting if any user hand-edits `%APPDATA%\razer-joro\config.toml` and then saves via the UI — their comments will be lost.

**Open / next session:**
- Webview may cache the old HTML via WebView2's persistent cache. If the UI doesn't show the new badge/dropdown on first open after this build, a hard reload (close & reopen settings, or Ctrl+Shift+R in the webview) fixes it.
- Long Fn holds briefly open Task View because Col03 emits consumer usage 0x029D when Fn is held. Suppression belongs in `consumer_hook.rs` — extend it to swallow 0x029D on BLE.
- Firmware-side "disable Hypershift layer" BLE command still undecoded. Only matters if user wants a host-side binding to override an already-written firmware entry on BLE alone. Low priority — transport cycle workaround works.
- Base-layer (plain non-Fn) USB writes still untested. Separate command, separate investigation.
- Cleanup pass: strip debug `eprintln!`, remove dead `is_media_vk` warning in keys.rs:110, `cargo build --release`.
- The `%APPDATA%\razer-joro\config.toml` live state has been rewritten by the UI save flow — `consumer_remap = []` is at the top from the re-serialization. Harmless but cosmetically messy.

## Session 2026-04-14 — Host-side Fn detection WORKING over BLE (Synapse parity achieved)

**Big one.** Daemon now replicates Synapse's "Hypershift over BLE" with zero USB. Implementation:

1. **Discovered the Fn signal.** Joro exposes Fn state on vendor HID collection `usage=0x0001/0x0000` (Col05 on BLE). 12-byte report: `[0x05, 0x04, state, 0...]` where state=0x01 on Fn down, 0x00 on Fn up. Verified by running `fn_detect::spawn_diagnostic()` on BLE and pressing Fn+keys while Synapse was dead and our daemon was the only thing reading HID. Capture: `captures/fn_detect_ble.log`. Rule-out: plain F5 → Col03 Mute with zero Col05 event.
2. **Proved Synapse doesn't write firmware keymap over BLE.** Phase 3 test: Synapse "programmed" Fn+A → LWin on BLE, then killed all Razer. With USB briefly plugged (daemon off), `diag-readlayers 0x1f` showed matrix 0x1F layer 1 = 0x04 ('a'), unchanged from factory. So Synapse's BLE Hypershift is host-side only — matches the 2026-04-10 commit 6b65ffe conclusion. New memory: `project_ble_keymap_is_hostside.md`.
3. **Discovered firmware Hypershift has a runtime enable flag** separate from stored data. Synapse on BLE turns the flag OFF; transport cycle turns it back ON. Our USB-written Home/End values stayed intact in layer 1 throughout — only the flag got toggled. Memory: `project_hypershift_runtime_enable_flag.md`.
4. **Implemented `fn_detect::start()`** — enumerates Joro HID, opens non-denied collections, spawns blocking reader threads, filters `05 04 xx` reports, updates `FN_HELD: AtomicBool`. Idempotent across device-connect events.
5. **Extended `remap.rs` hook_proc** — new top-level branch consulting `fn_detect::fn_held()` on key-down, lookup in `FN_HOST_REMAP_TABLE`, SendInput translation, tracked in `ACTIVE_FN_REMAP` so source-key-up releases the correct output even if Fn was released first.
6. **New config section `[[fn_host_remap]]`** — same schema as `[[fn_remap]]`. Applied at daemon startup, config reload, and UI save.
7. **Verified working**: clean BLE-only session, daemon started with seeded `Fn+A → F2`, File Explorer Rename triggered by Fn+A. Firmware Fn+Left=Home, Fn+Right=End still work alongside (different code path).

**Memory updates**:
- NEW: `project_host_side_fn_detection.md` (this session's win)
- NEW: `project_hypershift_runtime_enable_flag.md`
- NEW: `project_ble_keymap_is_hostside.md`
- NEW: `project_hypershift_commit_trigger.md`
- Superseded: `project_joro_keymap_deadend.md` (the "dead end" was wrong — writes work, need transport cycle to commit)

**Code comments cleaned up** 2026-04-13--2310:
- `src/usb.rs::set_layer_remap` doc-comment (removed "KNOWN DEAD END" and wrong "writes base layer" walkback)
- `src/main.rs::apply_fn_remaps` doc-comment
- `src/keys.rs` matrix-index comment
- `src/main.rs` line 917 comment claiming Fn key was invisible to HID — REMOVED and replaced with live `fn_detect::start()` call.

**Still open (lower priority)**:
- `[[fn_remap]]` (firmware path) still requires USB connection to write. Daemon logs a user-visible "plug in USB" notice when trying to apply over BLE. Acceptable per user.
- Col03 fires consumer usage 0x029D on every Fn press — Windows normally ignores it but long Fn holds may briefly open Task View. Fix: extend `consumer_hook.rs` to swallow 0x029D on BLE.
- Col06 readable but emits no reports. Unknown purpose. Ignored.
- Base-layer writes (plain non-Fn remaps) over USB untested. Probably `cmd=0x0F` / `set_keymap_entry` or different `args[2]` value. Separate question.
- BLE "disable Hypershift layer" command (the one Synapse sends) not decoded. Only matters if we want host-side Fn bindings to override firmware Fn bindings from BLE alone. Low priority — current flow works.
- Cleanup pass: strip debug `eprintln!`, `cargo build --release`.

## Session 2026-04-13--2257 — Hypershift commit trigger found + BLE/wired share storage

**The "keymap dead end" from session 2154 was wrong.** `set_layer_remap` (cmd=0x0d) works perfectly. The missing piece was a **commit trigger: a transport mode switch** (wired↔BLE). Firmware stores writes immediately but only refreshes the runtime Hypershift table when transport changes.

**Sequence that proved it this session:**
1. Killed all Razer services (Chroma SDKs, Elevation, Stream, Game Manager) + all Razer/Synapse processes. User reset Joro via Synapse beforehand for clean baseline.
2. Wired USB. Started daemon → `apply_fn_remaps` wrote Left→Home (matrix=0x4f, HID 0x4a) and Right→End (matrix=0x59, HID 0x4d) via cmd=0x0d. Daemon log showed OK. Tested Fn+Left on wired — still plain arrow. Looked like "dead end" reproducing.
3. Switched to BLE (pair was broken from earlier session, couldn't test there).
4. Switched back to wired. **Fn+Left=Home, Fn+Right=End.** Writes were live. The transport cycle committed them.

**Then: BLE/wired share the same Hypershift storage slot (new finding).**
- Nuked stale Joro BLE PnP records: removed `BTHLE\DEV_C8E2775D2FA2` and every `BTHLEDEVICE\...C8E2775D2FA2` child via `pnputil /remove-device`. Windows UI still showed Joro paired (cached). Root cause: Joro was paired via the BARROT Bluetooth 5.4 dongle (not the Intel radio), and BARROT was in `CM_PROB_FAILED_ADD` — `BluetoothRemoveDevice` WinAPI returned NOT_FOUND because it queried Intel. User physically unplugged+replugged BARROT → Windows UI cleared the stale entry.
- User re-paired Joro cleanly. New MAC `C8E2775D2FA3` (random address rotated from `...2FA2`). All BTHLEDEVICE children Present/OK.
- Daemon connected over BLE — no "object closed" errors, GATT stable, firmware v1.2.2.0. BLE intentionally skips firmware writes (`main.rs:176` guards `apply_fn_remaps` on USB only).
- **User tested Fn+Left / Fn+Right on BLE: both working** (Home/End), i.e. reading the values written over USB earlier. One USB write programs both transports.

**Memory updated:** `project_hypershift_commit_trigger.md` (new, authoritative) supersedes `project_joro_keymap_deadend.md`.

**Open questions / follow-ups:**
- BARROT 5.4 dongle is still in `CM_PROB_FAILED_ADD` — independent driver/firmware issue, not Joro-related. Joro is now paired via the Intel radio. Debug BARROT separately if user wants it back.
- Find an explicit "reload keymap" packet so a transport cycle isn't required for changes to go live. Look in `captures/synapse_hypershift_u3.pcap` for any non-cmd=0x0d traffic Synapse sends after a Hypershift write. Low priority since the current flow works.
- Update `apply_fn_remaps` doc-comment in `src/main.rs:253` — it still has the outdated "writes to base layer" misinformation.

## Session 2026-04-13--2154 — Keymap reverse engineering hit a dead end

Tried to restore Fn+Left=Home / Fn+Right=End without Synapse. Found that our `set_layer_remap` (`class=0x02 cmd=0x0d`) packet is byte-for-byte identical to Synapse's Hypershift-tab write, firmware accepts it with `status=0x02 OK`, and `cmd=0x8d` readback confirms the value persists in a "layer 1" storage slot — but the live keymap is unaffected. Plain Left still moves the cursor; Fn+Left still moves the cursor. Synapse's identical packets DO take effect live. Something else Synapse sends commits/reloads the runtime keymap and we haven't identified it.

**Full findings and next-session leads** saved to memory: `project_joro_keymap_deadend.md`. Key points:
- Firmware has ≥4 layer slots accessed via `args[2]` in cmd=0x0d/0x8d. Layer 0 appears to be factory default (HID 0x50 for Left); our writes went to layers 1+ and were stored but inert.
- Ruled out: trans_id (tried rotating), cmd=0xa4 "unlock", 20× 0x81 magic writes, cmd=0x0f `set_keymap_entry` alt path, write retry.
- Leads: openrazer source for commit semantics, hidapi vs rusb, DeviceIoControl direct to `RzDev_02cd`, full Razer uninstall to see if filter driver gates commits.

Left `cargo run -- diag-readlayers [0xMM]` in main.rs for future debugging. Removed all other temporary CLI subcommands.

## Session 2026-04-13--2041 — Copilot BLE regression RESOLVED

**Root cause: Razer Elevation Service was stopped.** Earlier in today's session we killed all 6 Razer services. The Chroma services were later restarted but `Razer Elevation Service` (Manual start type) was not. That service is the one that translates Joro's Copilot-key HID report into Win+Shift+F23 — without it, pressing Copilot produces zero events at WH_KEYBOARD_LL. Restarting it via `Start-Service 'Razer Elevation Service'` immediately restored the Win+Copilot → Ctrl+F12 remap.

Not a code regression. `src/consumer_hook.rs` (suspect #1) was a red herring — `ConsumerHook::start()` returns `None` when the config's `consumer_remap` list is empty, so no HID opens happen. The `single→single` reclassification in `src/remap.rs` only affects non-combo remaps. Unconditional debug log at `remap.rs:285` confirms the hook was alive; the key was simply not reaching Windows VK input at all.

Memory saved: `project_copilot_needs_razer_elevation.md`.

## Current milestone
Stage 5+++ (session ending 2026-04-13 evening): **BLE F1/F2/F3 firmware-locked confirmed by testing Synapse itself.** Copilot regression resolved (Razer Elevation Service was stopped — see above). Next: tasks 6–9 from the TODO list.

**Working tree has uncommitted edits across ~14 files since last commit.** Session ended mid-task-7 with keys.rs changes for media VKs written but not built/tested. See TODO for full list.

## Session 2026-04-13 (end of day) — Copilot regression + per-key MM UI planning

### Copilot → Ctrl+F12 broken over BLE (REGRESSION — NOT A HARD LIMIT)
User reports this remap worked over BLE in an earlier session. Today in BLE mode:
- Daemon loads the trigger correctly: `gate=0x5B trigger=0x86 prefix=[0xA0] -> mods=[0xA2] key=0x7B` (visible in daemon startup log).
- Pressing the Copilot key produces ZERO events visible to WH_KEYBOARD_LL — no `0x86`, no Win+Shift+F23 pattern, nothing.
- Hook debug log is full of ordinary typing events so the hook itself is alive and receiving events — it's specifically the Copilot key that's not reaching it.

**This was incorrectly documented as a "hard BLE limit" in mid-session.** That was a wrong conclusion reached by guessing. The truth is: **we don't know what broke it**. User is right — needs actual diagnosis. Marked as task #10 / TOP-PRIORITY for next session.

### Suspect list for the Copilot regression (next session)
1. **`src/consumer_hook.rs` (NEW this session)** — opens Joro's Consumer Control (0x0C/0x01) AND System Control (0x01/0x80) HID collections. On Windows, reading a HID collection via `ReadFile` drains reports from that collection, so if Copilot's report goes through either of those collections, our thread could be stealing it before Windows' Copilot handler sees it. BIGGEST SUSPECT. First thing to try: disable the System Control open (it was added later, specifically for F4 which turned out to be a keyboard macro anyway — unused). Rebuild, retest Copilot.
2. **`src/remap.rs` single→single reclassification** — previously skipped, now pushes to combo_table. Shouldn't affect combo-source triggers like Win+Copilot but verify.
3. **Razer services being killed** — today we stopped all 6 Razer services (Chroma SDK Diagnostic/Server/Service, Chroma Stream Server, Elevation Service, Game Manager Service 3) and restarted them only once for the Synapse test. Current state: not running. Unknown whether BLE Copilot handling depends on them.
4. **Joro firmware state** — we ran many scans earlier today that wrote to base-layer keymap at matrix indices 0x01..0x82. Profile was reset once but we've written more since. Possible firmware state corruption around certain keys.

### Other definitively established facts this session (keep these)
- **BLE F1/F2/F3 = firmware-locked as slot selectors.** VERIFIED by running Synapse in BLE mode with Function Keys Primary enabled — slot switching still wins. Not circumventable.
- **In wired mode F1/F2/F3 CAN be translated to function keys** via host-side SendInput (Synapse does this).
- **F4 is a firmware keyboard macro emitting Win+Tab.** Interceptable via WH_KEYBOARD_LL combo-source trigger (already working, shipped). Currently removed from config per user request (user will pick a different key for rename).
- **F5 through F9 emit standard Consumer Control usages**: F5=0x00E2 Mute, F6=0x00EA VolDown, F7=0x00E9 VolUp, F8=0x0070 BrightnessDown, F9=0x006F BrightnessUp. F10-F12 TBD.
- **Synapse mm↔fn primary setting is a Synapse host-side feature**, not a firmware command. Clean USBPcap capture showed zero class=0x02 traffic during the toggle.
- **openrazer `class=0x02 cmd=0x06 fn_key_toggle` does NOT apply to Joro** (sysfs attr not registered for Joro's PID in razerkbd_driver.c:5307).

### Working tree state at session end
- `src/keys.rs` — media VK names added (VolumeMute..LaunchApp2). Compiles? **Unverified** — the last `cargo build` was interrupted. Next session: verify build.
- `src/remap.rs` — single→single reclassification, `make_key_input`/`send_inputs` exposed as `pub(crate)`, test updated. Compiles.
- `src/consumer_hook.rs` — new module. Compiles. Lifecycle wired to main.rs. `consumer_remap = []` in user config so it's inactive (no thread spawned).
- `src/main.rs` — consumer_hook lifecycle, `run_matrix_scan` CLI subcommand, `set_fn_key_toggle` removed.
- `src/device.rs`, `src/usb.rs`, `src/config.rs` — various small additions/removals.
- `assets/settings.html` — F-row `fwEmits`/`fwMedia`/`fwNote` metadata, popover prefill + hint.
- `%APPDATA%\razer-joro\config.toml` — F4 Win+Tab remap removed; F2 matrix remap still present.
- All three project docs (`_status.md`, `CHANGELOG.md`, `WORKPLAN.md`) updated.

### TODO / tasks for next session (in priority order)

1. **[TASK #10 — TOP PRIORITY] Diagnose Copilot BLE regression.** See task description — start by commenting out System Control HID open in `src/consumer_hook.rs::open_input_interfaces()`, rebuild, retest. If that doesn't fix it, `git stash` the working tree, rebuild from commit `dbb4511`, and test Copilot over BLE. If it works there, bisect.
2. **[TASK #6] Extend keys.rs with media VK names** — code already written in the working tree but not built. Verify build, verify `parse_key_combo("VolumeMute")` returns `Some((vec![], 0xAD))`.
3. **[TASK #7] Verify single→single media-VK remap path** — add `[[remap]] from="VolumeMute" to="F5"` to config, daemon restart, press F5 in mm-primary mode, confirm it emits VK_F5 (not mute). Don't trust earlier "this is already done" claim until actually tested.
4. **[TASK #8] UI: per-key MM override for F5–F12** — update `findRemapForKey` to also match against `fwMedia`; update popover prefill so clicking F5 defaults `From = VolumeMute`; add an orange warning for F8/F9 (brightness VKs bypass LL hook). Only after task 10 is resolved.
5. **[TASK #9] UI: "Function Keys Primary" preset button** — one-click that writes/clears 6 `[[remap]]` entries for the canonical media-VK → F-key mapping.
6. **Icon redraw** (flagged earlier) — current PIL-generated ICO looks pixelated. Low priority.
7. **Strip debug `eprintln!`, remove `fn_detect` module, `cargo build --release`** — cleanup pass.

## Session 2026-04-13 (late) — Definitive BLE slot finding

### The test
User launched Razer Synapse, put Joro in **BLE mode**, enabled **Function Keys Primary** in Synapse, and pressed F1/F2/F3 in Notepad and Explorer. Result: **slot switching fired on all three, regardless of Synapse's fn/mm primary toggle**. In wired mode, Synapse's fn-primary toggle DID make F1/F2/F3 emit function-key VKs.

### Hard limits now established
- **BLE mode**: F1/F2/F3 are firmware-locked to BLE slot switching. No command (Synapse has none, openrazer has none, we have none) overrides this.
- **Wired mode**: F1/F2/F3 emit nothing by default, but can be host-side translated to VK_F1/F2/F3 via SendInput when fn-primary is on. Synapse confirms this pattern.
- Therefore: user's personal target **"BLE connection + plain F2 = rename"** is **impossible** without a firmware patch. The only no-loss alternatives are:
  - (A) Stay on BLE; use `Fn+F2 = rename` (already working via matrix remap 0x71 → 0x3B).
  - (B) Switch to wired; add host-side `F2 → F2` via VK-level intercept once we build the per-key MM remap UI.
  - (C) Use a different physical key for rename (e.g. Copilot, Fn+some letter).

### Implications for the project
- **Scope narrowed** per user instruction: target is Synapse parity for **fn/mm primary toggle + full Hypershift remapping only**. NOT gaming features (keyswitch optimization, scroll wheel, macros).
- fn/mm primary = host-side VK interception layer (no firmware mechanism exists on Joro).
- Hypershift = firmware matrix remap via `cmd=0x0d` with `args[0]=0x01` — already working, documented.
- F4 = rename (via Win+Tab intercept) stays as the currently shipped mechanism until user decides the long-term layout. May be reverted when per-key UI lands.

## Session 2026-04-13 (F4 = rename discovery + ship) Discovered via WH_KEYBOARD_LL hook debug logging after HID consumer/system interface reads came up empty for F4. Fix: add `[[remap]] from="Win+Tab" to="F2"` to config — the existing combo-source trigger path intercepts and emits F2. Trade-off: physical Win+Tab (Task View) is sacrificed. All other mm keys, BLE slot selection, Fn+F2=rename, lighting, and host combo remaps (Win+L, Copilot) remain intact. Consumer HID interception layer built but found to be non-consuming on Windows (hidapi reads shadow the reports but don't remove them from the stack) — kept in place for discovery logging but no active remaps.

## Session 2026-04-13 (F4 = rename discovery + ship)

### F4 investigation path
- Consumer HID discovery script (`proto/consumer_discover.py`) captured F5=Mute=0x00E2, F6=VolDown=0x00EA, F7=VolUp=0x00E9, F8=BrightnessDown=0x0070, F9=BrightnessUp=0x006F. F4/F10/F11/F12 produced no consumer reports.
- Built Rust consumer_hook (`src/consumer_hook.rs`) that opens both Consumer Control (0x000C/0x0001) and System Control (0x0001/0x0080) HID interfaces via hidapi, but F4 never appeared on either.
- Killing all 6 Razer services (`Razer Chroma SDK *`, `Razer Elevation Service`, `Razer Game Manager Service 3`) did not stop F4 from arranging windows — so no user-mode Razer component was handling it.
- Enabled WH_KEYBOARD_LL hook debug logging (already scaffolded in `src/remap.rs::dbg_log`) and captured a clean F4 press. Result: `DN vk=0x5B (LWin) scan=0x5B` immediately followed by `DN vk=0x09 (Tab) scan=0x0F`. **F4 is a firmware keyboard macro emitting Win+Tab.** That's why:
  - F4 never appeared on consumer/system HID interfaces — it's main-keyboard-interface keystrokes.
  - VK_F4 never appeared in any earlier hook debug dump.
  - Killing Razer services doesn't help — it's all in Joro firmware.
- Fix was then a single config entry: the existing `[[remap]] Win+L → Delete` combo-source trigger infrastructure can intercept any `<mod>+<trigger>` pair. Added `[[remap]] from="Win+Tab" to="F2"` and verified: pressing F4 in Explorer with a file selected puts the filename into rename mode. Task View is no longer triggered by F4 (or by physical Win+Tab — the sacrifice we accepted).

### Consumer HID layer status
- New module `src/consumer_hook.rs`: background thread opens Joro's Consumer + System HID interfaces, reads reports, matches against `[[consumer_remap]]` config entries, emits replacement keys via `SendInput`. Logs unknown usages so users can discover codes organically.
- `src/remap.rs`: `make_key_input` and `send_inputs` exposed as `pub(crate)` so `consumer_hook` can reuse them.
- `src/config.rs`: new `ConsumerRemapConfig` struct + `consumer_remap: Vec<_>` field.
- `src/main.rs`: `App::consumer_hook: Option<ConsumerHook>` lifecycled in `try_connect` / `check_device`.
- **Important caveat**: hidapi reads on Windows are **non-consuming** — we see consumer usages but Windows still routes them to its media-key handler. So intercepting Mute/Vol/Brightness via this layer DOES NOT swallow the original behavior (e.g. Mute still toggles even if we SendInput F12 as a replacement). The layer remains useful for discovery logging and for remapping usages that Windows ignores by default; for true intercept of mm keys, WH_KEYBOARD_LL at the VK level is needed (VK_VOLUME_*, VK_MEDIA_*).

### Code cleanup this session
- Removed the short-lived `set_fn_key_toggle` experiment (openrazer's `class=0x02 cmd=0x06` fn_key_toggle doesn't apply to Joro — `dev_attr_fn_toggle` is not registered for Joro's product ID in openrazer's `razerkbd_driver.c:5307`). The earlier transaction_id=0xFF retry also produced no effect. The Synapse "Multimedia Keys Primary" toggle is a Synapse host-side feature, not a firmware command — confirmed by a clean USBPcap capture of the mode-toggle UI action showing zero class=0x02 writes.
- `fn-primary <state>` CLI subcommand removed alongside.

### Current user config (active 2026-04-13)
- `[[remap]] Win+L → Delete` (host-side, WH_KEYBOARD_LL)
- `[[remap]] Win+Copilot → Ctrl+F12` (host-side)
- `[[remap]] Win+Tab → F2` (host-side — intercepts F4 firmware macro)
- `[[fn_remap]] F2 → F2` (firmware base-layer — enables Fn+F2 = rename as fallback)
- `[[consumer_remap]]` section empty (discovery mode only)
- Lighting: static, `#eee8e8`, brightness 81

### Next steps
- **Icons** still flagged from earlier session as "look like shit" — redraw needed.
- Optional: extend `remap.rs` to intercept VK_VOLUME_MUTE/UP/DOWN + VK_MEDIA_* so the consumer_hook layer can become truly consuming on those specific keys (user hasn't requested, deferred).
- Clean-up pass: strip noisy debug `eprintln!`, remove `fn_detect` module, release build.
- BLE mode testing for F4's Win+Tab behavior (does it still fire over BLE? should behave the same — firmware macro should be transport-agnostic — but not verified).
- Tray menu item for toggling the consumer_hook on/off during discovery sessions.

## Session 2026-04-13--1625 — Matrix discovery, protocol correction, BLE slot architecture

### Major findings
- **Joro matrix table ~75% mapped** via 5 sequential `scan <batch>` runs (0..4 covering matrix indices 0x01..0x82). See `src/keys.rs::JORO_MATRIX_TABLE`. Known keys now include full number row, Tab row, CapsLock row, shift row, bottom row (partial), arrow/nav cluster, F-row (F1..F12 = 0x70..0x7B), and Escape (0x6E). Remaining gaps: 0x3F, 0x41..0x45 (likely LAlt / PrintScreen / Pause / ScrollLock / Fn key), 0x52, 0x57, 0x58, plus anything past 0x7B.
- **Protocol understanding corrected.** Captured Synapse remapping Right Ctrl → F2 on the Standard (base) layer. Packet bytes:
  ```
  class=0x02 cmd=0x0d dsize=10 args = 01 40 01 02 02 00 3b 00 00 00
  ```
  The `args[0]=0x01` is a **constant**, not a layer selector. Our `set_fn_layer_remap` was always writing to the base layer — the "Fn-layer" name was a misconception. Renamed to `set_layer_remap`. Earlier apparent successes ("Fn+Left → Home works") were because:
  - In mm-primary mode, Fn key toggles F-row from media pipeline → matrix pipeline.
  - For non-F-row keys, Fn is a no-op, so the base remap was active regardless of Fn.
- **Deleted** `set_base_layer_remap` (it was writing `args[0]=0x00` which was wrong) and `apply_base_remaps`. Removed `[[base_remap]]` config section.
- **BLE slot selector architecture discovered.** In mm-primary mode + BLE transport, F1/F2/F3 tap emits a firmware-internal "switch BLE device N" action that runs BEFORE matrix lookup. We verified this via a controlled test: programmed F2 matrix (0x71) → HID F2 (0x3B), then tested in BLE mode. Result: **F1/F3 still switch slots** (untouched by matrix write), **F2 still switches slot 2** (base path bypasses matrix), **Fn+F2 = actual F2 key** (Fn-held path goes through matrix, our remap takes effect). Implications:
  - Matrix remaps are safe — they do not break BLE slot selection.
  - F-row base taps in mm-primary mode cannot be remapped via matrix at all.
  - To intercept F-row base taps in mm-primary mode (e.g. F4 "arrange windows"), must use host-side HID interception on the Consumer Control interface.
- **F2 → rename goal:** current compromise is `Fn+F2 = rename` (works via matrix remap at 0x71 → 0x3B, already in config). To get plain F2 = rename while preserving all mm defaults + BLE slots, a firmware-level per-key mode override would need to be discovered; unknown if Joro firmware exposes one.

### Code cleanup this session
- `src/usb.rs` — renamed `set_fn_layer_remap` → `set_layer_remap`; deleted `set_base_layer_remap`; updated docstring to reflect capture findings.
- `src/device.rs` — trait method renamed; `set_base_layer_remap` removed.
- `src/main.rs` — all callers updated; `apply_base_remaps` deleted.
- `src/config.rs` — `base_remap` field removed from `Config` struct and `DEFAULT_CONFIG`.
- `src/keys.rs` — `JORO_MATRIX_TABLE` extended from 4 entries to ~60 entries.
- `src/main.rs` — new `run_matrix_scan(batch)` CLI subcommand (`cargo run -- scan <n>`), programs 26 matrix indices to letters a..z for interactive discovery.

### Verified config state (persisted in user firmware this session)
- `[[fn_remap]] F2 → F2` (matrix 0x71 → HID 0x3B) — programmed on every USB connect. Enables Fn+F2 = rename in mm-primary mode.
- `[[fn_remap]] Left → Home` and `Right → End` — legacy from earlier session.
- Everything else (BLE slots, mm defaults, F4 arrange) remains factory.

### Next steps
- **Deeper firmware reversal for per-key mode override.** Goal: find a firmware command that moves F2 (specifically) from the mm/BLE-slot pipeline to the matrix pipeline while leaving F1/F3/F4/etc untouched. Approaches:
  - Capture Synapse with Joro in mm-primary but with F2 remapped to something — does Synapse send a different packet for F-row specifically?
  - Dump firmware memory via undocumented Razer debug commands (class=0x00 cmd=0x8X probes).
  - Brute-force unknown `class` / `cmd` bytes, watching for behavior changes.
- **Clean isolated captures still needed** for: (a) MM↔Fn primary mode toggle command (our earlier 2x `cmd=0xa4` capture was ambiguous — both writes had args=0), (b) Consumer-usage output encoding (how Synapse encodes media-key outputs in cmd=0x0d — needed for programming F-row mm overrides).
- **Matrix table remaining gaps:** 0x3F, 0x41..0x45, 0x52, 0x57, 0x58, >0x7B. Low priority since discovered keys cover all common needs.
- **F4 base tap interception** — if user wants F4 alone = rename, needs host-side Consumer HID reader thread in daemon. Deferred pending firmware reversal attempt.
- **Icons** — flagged as "look like shit" earlier in session. Need to redraw at better quality (64x64 source instead of 256x256 downscale, or replace the PIL generator with a proper vector asset).

## Last session continued (2026-04-13 03:09 PDT) — Config-driven Fn remaps + battery fix

### Completed this round
- **Fn-layer remap is now config-driven** via new `[[fn_remap]]` section in `config.toml`. Daemon iterates entries and calls `set_fn_layer_remap()` for each on USB connect. Removed hardcoded Left/Right calls from `try_connect`.
- **`keys::key_name_to_matrix(name)`** lookup table for Joro physical-key matrix indices. Currently knows: `Escape=0x01`, `CapsLock=0x1E`, `Left=0x4F`, `Right=0x59` (~4 of ~85 keys — discovery is Phase 3).
- **`parse_hid_combo()`** in `main.rs` — converts strings like `"Home"`, `"Ctrl+F12"`, `"Shift+End"` into the `(modifier_byte, hid_usage)` pair that `set_fn_layer_remap` expects. Supports HID modifier bits for LCtrl/LShift/LAlt/LGui/RCtrl/RShift/RAlt/RGui.
- **Battery fix**: was reading `arg[0]` (first byte of response data) but openrazer driver source confirms battery level is in `arg[1]`. Updated both BLE (`src/ble.rs::get_battery_percent`) and USB (new `src/usb.rs::get_battery_percent` + trait override). USB shows real battery now.

### Files changed this round
- `src/keys.rs` — `JORO_MATRIX_TABLE` + `JORO_MATRIX_MAP` + `key_name_to_matrix()`
- `src/config.rs` — `FnRemapConfig` struct, `Config::fn_remap` field, default config seeds Fn+Left/Fn+Right entries
- `src/main.rs` — `apply_fn_remaps()` static method, `parse_hid_combo()` helper, `try_connect` now calls `apply_fn_remaps` instead of hardcoded calls
- `src/ble.rs` — battery now reads `data.get(1)` per openrazer
- `src/usb.rs` — new `get_battery_percent()` method + trait override

## Previous round (2026-04-13 02:48 PDT) — Fn-layer remap protocol REVERSE-ENGINEERED + WORKING

### Big win: Fn-layer firmware keymap programming
- **Reverse-engineered Razer Synapse's Fn-layer remap command via USB capture** (USBPcap on USBPcap3 + Synapse remap session in Wireshark, parsed with custom Python pcap parser).
- **Protocol**: `class=0x02 cmd=0x0d` with 10-byte data payload:
  ```
  args[0]  = 0x01           // layer selector (1 = Fn layer)
  args[1]  = src_matrix     // Razer matrix index of source key
  args[2]  = 0x01           // var-store / profile (constant from capture)
  args[3]  = 0x02           // output type (HID keyboard)
  args[4]  = 0x02           // output payload size
  args[5]  = modifier       // HID modifier byte (0 for plain key)
  args[6]  = dst_usage      // HID keyboard usage code
  args[7..10] = 0x00        // padding
  ```
- **No setup or commit command needed** — Synapse just sends the raw remap and firmware persists it. Confirmed by inspecting the timeline of all SET_REPORT control transfers in the capture (1532 lighting frames + 3 remap frames + 2 routine queries; nothing else).
- **Joro matrix indices identified so far**:
  - Left arrow: `0x4F`
  - Right arrow: `0x59`
  - (full table TBD via additional captures or brute force)
- **HID usage codes used in test**:
  - Home: `0x4A`
  - End: `0x4D`
- **Implementation**: `RazerDevice::set_fn_layer_remap(src_matrix, modifier, dst_usage)` in `src/usb.rs`, exposed via `JoroDevice::set_fn_layer_remap` trait method (default impl returns Err for non-USB transports).
- **Verified working**: Fn+Left → Home, Fn+Right → End both confirmed in Notepad immediately after the daemon applied the remaps. **Persists in firmware** — works on BLE / 2.4GHz / other PCs without re-applying.

### How to use right now
Hardcoded in `try_connect()`: applies Fn+Left → Home and Fn+Right → End on every USB connect. The keyboard firmware persists them, so once-applied is enough — but re-applying is idempotent and safe.

### Files changed
- `src/usb.rs` — new `set_fn_layer_remap()` method on `RazerDevice` and trait override
- `src/device.rs` — new `set_fn_layer_remap()` trait method (default Err)
- `src/main.rs` — applies hardcoded Fn+Left/Right remaps on USB connect

### Capture/parse infrastructure (kept for future captures)
- `C:\Users\mklod\AppData\Local\Temp\synapse_capture.ps1` — launches USBPcap on all 3 root hubs in parallel via PowerShell Start-Process (the only invocation pattern that works headless)
- `C:\Users\mklod\AppData\Local\Temp\parse_synapse2.py` — pcap parser that decodes Razer Protocol30 SET_REPORT control transfers and groups by (class, cmd) for easy command discovery
- `C:\Users\mklod\AppData\Local\Temp\synapse_capture.pcap` — the raw capture (441KB), retained for re-analysis if needed

### Next steps for Fn remaps
- **Generalize**: move the hardcoded Fn+Left/Right remap calls into config-driven entries (e.g., `[[fn_remap]] from = "Left", to = "Home"` in `config.toml`)
- **Matrix index discovery**: do additional Synapse captures to map every Joro key's matrix index, save to a lookup table in `keys.rs`
- **UI exposure**: add a "Fn Layer" toggle to the visual keyboard in the settings window so user can click any key and assign a Fn-layer combo via firmware programming
- **Combo outputs**: test that `modifier` byte works (e.g., Fn+L → Ctrl+F12 by setting modifier=0x01 (LCtrl), dst_usage=0x45)
- **Base layer experimentation**: try `args[0] = 0x00` to see if it programs the base layer (effectively replacing `set_keymap_entry` cmd 0x0F with cmd 0x0D)

## Last session (2026-04-13 01:59 PDT) — Webview settings window, Fn detection (blocked, now resolved)

### Completed
- **wry webview settings window** (`src/settings_window.rs`) — 1100×640 fixed-size non-resizable window, position persisted to `%APPDATA%\razer-joro\window_state.json`, opened via left-click on tray OR tray "Settings…" menu, right-click still shows context menu.
- **Visual 75% Joro keyboard** (`assets/settings.html`) — flex-based layout with:
  - Inline SVG icons for F-row (bluetooth, screens, speaker mute/low/high, sun small/large, backlight down/up, padlock)
  - Copilot (real icons8 Microsoft Copilot path), Windows 4-pane logo, globe (fn), chevron arrow keys
  - Media control icons (prev/next/play/mute) on pg up / pg dn / home / end
  - Subtle green glow outline for mapped keys (dark background preserved), solid green fill when popover is active
  - BT LED dots on F1/F2/F3, CapsLock LED dot, sharp white outer border
  - Per-key alignment variants: `align-left` (Tab, CapsLock, LShift), `align-right` (Enter, Backspace), `align-center` (F-row, arrow cluster), default top-center (everything else)
  - F-row 34px shorter than 60px main rows; 6px gap between top/bot labels
- **Exact-match remap engine** — each keyboard key has an `emits` field; `findRemapForKey()` does case-insensitive exact match on the remap's `from` field. Lock key emits `Win+L`, Copilot key emits `Win+Copilot`, so those combos highlight the correct physical key (not the letter L, not the Win key).
- **Popover remap editor** — click any key → small popover with editable From/To fields, defaults the From to the key's `emits` value. Save/Clear/Cancel, auto-save on confirm. Modifier combos like `Ctrl+F5`, `Win+L` work directly.
- **Single-key → single-key host remap fixed** — `remap.rs` previously silently dropped `a → b` style remaps (intended for firmware path but never wired). Removed the skip; now host-side hook handles them.
- **Lighting controls in settings window** — single-row layout (Color picker → Brightness slider → Effect dropdown) with auto-save IPC. New `set_lighting` action in `handle_settings_ipc` updates config + file + device live.
- **Battery indicator** — new `get_battery_percent()` on `JoroDevice` trait, BLE impl reads Protocol30 `0x07/0x80` and maps raw byte to 0-100%. Shown as SVG battery icon + percentage in top-right of window header.
- **Auto-connect to paired Joro** (major reconnect fix) — on startup, `find_paired_joro()` enumerates paired BLE devices via `DeviceInformation::FindAllAsyncAqsFilter(BluetoothLEDevice::GetDeviceSelectorFromPairingState(true))` and opens the Joro by device ID WITHOUT waiting for advertisements. The previous advertisement-watcher path didn't work for already-paired+connected devices. Now the daemon reconnects in <1s on startup with no re-pair dance.
- **Clean shutdown via Ctrl+C** — `ctrlc` crate + `shutdown_and_exit()` method: explicitly drops the BleDevice (which runs `Close()` on WinRT and releases the keyboard), then `std::process::exit(0)` because winit's `run_app` sometimes won't return after `event_loop.exit()` when wry windows are alive.
- **Unified UI tray** — Color/Brightness/Effect submenus still in tray; "Settings…" opens the webview window; reconnect backoff 10s when disconnected (was 2s — was blocking the tray during scans).

### Fn-arrow investigation (blocked)
Goal: `Fn+Left → Home` and `Fn+Right → End` as host-side intercepted combos (Synapse supports this via firmware programming).

- **Confirmed:** Joro's Fn key produces NO VK via `WH_KEYBOARD_LL`. Pressing Fn alone → zero events. Pressing Fn+Left → plain `VK_LEFT (0x25) scan=0x4B`, indistinguishable from Left alone. Fn+F5 DOES produce "AC Refresh" (0x29D) on the Consumer Control HID interface because the firmware translates it internally.
- **Built `src/fn_detect.rs`** — hidapi-based diagnostic that enumerates all HID devices matching VID 0x1532 OR product name "Joro" OR path containing "razer", opens each readable interface, timestamps every input report for visual correlation with keypresses.
- **BLE Joro exposes 6 HID collections** (vid 0x068e pid 0x02ce when on BLE — assigned Bluetooth VID, not USB 0x1532):
  - `[0]` Keyboard (Col01, usage 0x0001/0x0006) — **access denied**, Windows owns it
  - `[1]` Mouse (Col02, 0x0001/0x0002) — **access denied**
  - `[2]` Consumer Control (Col03, 0x000C/0x0001) — readable; reports `03 9d 02 00` on Fn+F5 = AC Refresh
  - `[3]` System Control (Col04, 0x0001/0x0080) — readable, no reports seen during test
  - `[4]` Vendor/Generic (Col05, 0x0001/0x0000) — readable; reports `05 04 01 00` on Fn+F5 (paired with [2])
  - `[5]` Vendor/Generic (Col06, 0x0001/0x0000) — readable, no reports seen
- **Fn alone, Fn+Left, plain letters** all produced **zero reports** on the readable interfaces. So no byte in any readable HID collection carries Fn-held state.
- **Synapse must be doing firmware-level Fn-layer keymap programming** (writing to class 0x02 sub-command for Fn-layer entries). We haven't reverse-engineered that protocol. Alternatives would be: install a kernel-level filter driver (like Interception) to get exclusive keyboard access, or capture Synapse's USB writes when it remaps Fn+Left in a VM with Synapse installed.

### Key discoveries this session
1. **wry + winit Drop order** — the webview field must be declared BEFORE the window field in `SettingsWindow` struct so drops run in the right order. Window drop before webview causes WebView2 to panic cleaning up against a destroyed HWND.
2. **`event_loop.exit()` doesn't always return from `run_app`** when a tray icon / webview is registered. Ctrl+C handler must explicitly drop state and call `std::process::exit(0)` as a fallback.
3. **Paired-device enumeration >> advertisement watching** — for any BLE device that's already paired to Windows, `DeviceInformation::FindAllAsyncAqsFilter(GetDeviceSelectorFromPairingState(true))` resolves in milliseconds without needing the device to advertise. Our advertisement-watcher-first approach was fundamentally wrong for reconnect scenarios.
4. **Joro Fn key is completely invisible to user-space** — no WH_KEYBOARD_LL events, no readable HID vendor reports (at least not on the interfaces Windows doesn't own). Synapse must use kernel filter drivers or firmware keymap programming.

### Files changed / added
- `assets/settings.html` — entire webview UI (keyboard visual, popover, lighting, tabs removed, battery indicator)
- `src/settings_window.rs` (new) — wry window lifecycle, position persistence
- `src/window_state.rs` (new) — tiny JSON read/write for settings window position
- `src/fn_detect.rs` (new) — hidapi diagnostic for Joro HID report inspection
- `src/main.rs` — `UserEvent::CtrlC/SettingsIpc`, `apply_lighting_change`, `shutdown_and_exit`, tray left-click handler, reconnect backoff, paired-device auto-connect wiring
- `src/ble.rs` — `find_paired_joro` + `connect_from_device` + `connect_from_address`, `get_battery_percent`
- `src/tray.rs` — `with_menu_on_left_click(false)`, `poll_tray_event`, "Settings…" menu item, `menu_settings_id`
- `src/device.rs` — `get_battery_percent` default trait method
- `src/remap.rs` — removed silent skip for single-key single-key remaps
- `src/config.rs` — `save_remaps` helper for webview save action
- `Cargo.toml` — added `wry`, `serde_json`, `ctrlc`, `hidapi`; windows crate features expanded (`Devices_Bluetooth_Advertisement`, `Devices_Enumeration`, `Foundation`, `Foundation_Collections`, `Storage_Streams`)

## Next immediate task
- **Fn+arrow remap investigation**: two real paths forward —
  1. **Firmware keymap reverse-engineering**: install Synapse in a VM, USB-capture its traffic while remapping Fn+Left → Home, identify the class/command/sub-command that writes Fn-layer entries, implement the same over our USB path (class 0x02 extension).
  2. **Accept constraint**: tell user Fn+arrows cannot be host-side intercepted, offer Right Alt / Right Ctrl / CapsLock as alternative hyper-modifiers that DO emit VKs and work with the existing trigger remap engine.
- Visual keyboard polish: user will iterate further on outlines, font, alignment, backlight icons based on feedback
- Test USB↔BLE mid-session transport switch (still unverified after BLE rewrite)

## Previous session (2026-04-12) — Interactive systray UI, config hot-reload, effect modes

### Completed
- **Tray submenus: Color / Brightness / Effect** using `CheckMenuItem` for active-selection checkmarks
  - 8 color presets (red/orange/yellow/green/cyan/blue/purple/white)
  - 4 brightness presets (25/50/75/100%)
  - 3 effect modes (Static / Breathing / Spectrum)
  - Clicking a preset: updates in-memory config → writes targeted TOML line (preserves comments) → applies to device → syncs checkmarks → bumps mtime watermark so config-poll doesn't double-fire
- **`apply_config()` branches on `lighting.mode`** — static / breathing / spectrum all wired through the `JoroDevice` trait
- **`JoroDevice` trait** gained `set_effect_breathing` and `set_effect_spectrum` with default implementations (USB falls back to static color, BLE calls the real effect methods)
- **`config::save_lighting_field()`** — targeted in-place TOML line editor that preserves comments and other sections, used by tray preset handlers
- **Status line** now shows transport: `Razer Joro — Connected (BLE)` or `(USB)`
- **Hot reload over BLE verified** — edit config.toml color mid-run, daemon's 5s config poll picks up the mtime change, reload_config() reapplies, tray checkmarks sync
- **Dead-code warnings all cleaned** — per-item `#[allow(dead_code)]` on forward-compat items (dongle detection, keymap helpers, etc.)

### Key discovery
- **Joro BLE pairing state matters.** If Windows has a stale/half-paired record of the keyboard (previous address, incomplete pair), the daemon's WinRT GATT session behaves erratically — initial connect + firmware read succeed, but subsequent GATT writes fail with `HRESULT(0x80000013) "The object has been closed."` and ConnectionStatus flaps. Fix: remove the device from Windows Bluetooth settings, put the keyboard back in BLE pairing mode, complete Windows' Add Device flow. After a clean pair, tray preset writes work reliably.
- **MaintainConnection=true genuinely holds** the session when pairing is clean. The earlier "btleplug doesn't honor MC" theory was a combination of btleplug's missing flag AND the unclean pairing state confusing WinRT.

### Files changed
- `src/tray.rs` — submenu infrastructure, preset tables, `CheckMenuItem` arrays, `match_color/brightness/effect`, `sync_check_state`, transport-aware status line
- `src/config.rs` — `save_lighting_field()` helper; removed `#[allow(dead_code)]` on `mode`
- `src/device.rs` — trait methods `set_effect_breathing`, `set_effect_spectrum` with default fallbacks
- `src/ble.rs` — BLE impl overrides the default effect trait methods; unlocked breathing/spectrum dead-code
- `src/main.rs` — `Preset` enum, `apply_preset()` handler, `handle_menu_events` routes through `match_*`, mtime watermark bump after tray writes, `apply_config` branches on mode
- `Cargo.toml` — unchanged (tray-icon was already present)

## Next immediate task
- Test USB↔BLE mid-session transport switch (flip mode toggle on keyboard while daemon is running)
- Optional: strip remaining debug `eprintln!` from ble.rs once behavior confirmed stable
- Stage 5 continued (optional): webview settings window via wry for custom color picker, smooth sliders, visual keymap editor

## Previous session (2026-04-12 earlier) — Replaced btleplug with direct WinRT, BLE is stable

### Completed
- **Replaced btleplug with direct WinRT** — `src/ble.rs` now uses the `windows` crate directly:
  - `BluetoothLEAdvertisementWatcher` for discovery (filter on LocalName == "Joro")
  - `BluetoothLEDevice::FromBluetoothAddressAsync` for device acquisition
  - `GattSession::FromDeviceIdAsync` + `SetMaintainConnection(true)` — the setting we held throughout the connection lifetime (instead of btleplug's default-false session)
  - `GattDeviceService::GetCharacteristicsForUuidAsync` for char_tx/char_rx discovery
  - `WriteClientCharacteristicConfigurationDescriptorAsync(Notify)` + `ValueChanged` handler for notifications
  - All GATT writes via `WriteValueWithResultAndOptionAsync(WriteWithResponse)`
  - `BluetoothLEDevice::Close()` on Drop so the keyboard resumes advertising after disconnect
- **`JoroDevice` trait refactor** — USB + BLE behind a single `Box<dyn JoroDevice>` field in `main.rs`. `try_connect`, `apply_config`, `check_device`, `reload_config` all backend-agnostic.
- **`is_connected` tolerance** — reads `BluetoothLEDevice.ConnectionStatus` (cheap property, not GATT). Windows flaps between Connected/Disconnected momentarily; we require 3 consecutive false readings before declaring disconnected. Absorbs the ~10s of post-connect flap cleanly.
- **Removed dependencies** — btleplug, tokio, futures, uuid all gone. Cargo.toml is significantly slimmer.
- **Visual verification** — green→red→blue config changes all applied and visually confirmed on hardware over BLE
- **Sleep/wake cycle verified** — daemon holds connection until keyboard's firmware sleep timeout fires, then cleanly Drops, scans, and reconnects immediately when user presses a key

### Key discoveries (WinRT BLE)
1. **btleplug 0.12 has no `MaintainConnection=true`** on its GattSession → connections die within 1-2 seconds. Fixable only by owning WinRT directly.
2. **Acquiring a side GattSession to set MaintainConnection doesn't work** — WinRT GattSession ties to the calling process's device handle; a side session held in our code doesn't affect btleplug's internal session.
3. **Windows `ConnectionStatus` property flaps** for ~5-10s after connect even on stable hardware. Treat any single `Disconnected` reading as transient; only act on a run of them.
4. **Windows BLE advertisement cache** includes stale addresses from previously-paired devices — the old MITM proxy kept showing up as "Joro" until physically unplugged. Filter by LocalName in the advertisement watcher.
5. **Keyboard's firmware inactivity timeout overrides Windows MaintainConnection** — Windows pings the device, but the keyboard's own power management will still disconnect after some idle period. Expected behavior; handled by reconnect loop.
6. **`BluetoothLEDevice::Close()` on Drop is essential** — without it, Windows holds the link and the keyboard can stay invisible to scans for minutes after a daemon disconnect.

### Files changed
- `src/ble.rs` — complete rewrite using `windows` crate, no btleplug/tokio
- `src/device.rs` (new) — `JoroDevice` trait
- `src/usb.rs` — methods bumped to `&mut self`, `JoroDevice` impl
- `src/main.rs` — single `Box<dyn JoroDevice>` field, collapsed duplicated apply/check paths
- `Cargo.toml` — removed btleplug, tokio, futures, uuid; added `windows` features `Devices_Bluetooth_Advertisement`, `Devices_Enumeration`, `Foundation`, `Foundation_Collections`, `Storage_Streams`

## Next immediate task
- Test USB↔BLE mode switch on the keyboard while daemon is running (switch from BLE to USB mid-session, verify daemon picks up the new transport)
- Test config.toml hot-reload over BLE (edit color, verify daemon reapplies without reconnect cycle)
- Clean up dead code warnings in `ble.rs` (unused effects like get_brightness, set_breathing_*, etc.)
- Strip debug eprintln! from ble.rs once behavior is confirmed stable over a few sessions

## Previous session (2026-04-12) — Rust btleplug BLE End-to-End Working (superseded)

### Completed
- **Python bleak direct control script** — `scripts/ble_direct_control.py`, validates full protocol without MITM proxy (brightness GET/SET, RGB static, spectrum cycling all verified)
- **btleplug stale candidate fix** — scan returns cached+live addresses; now iterates all "Joro" candidates and tries each until connect succeeds
- **btleplug MaintainConnection fix** — btleplug 0.12 does NOT set `GattSession.MaintainConnection`, so WinRT drops the GATT session ~seconds after connect. Fixed by directly creating a `windows::Devices::Bluetooth::GenericAttributeProfile::GattSession` from the Bluetooth address and calling `SetMaintainConnection(true)` after btleplug connects. Session stays alive indefinitely.
- **is_connected() fallback** — WinRT cached connection status can lag; added GATT read fallback
- **End-to-end verified** — daemon reads firmware (v1.2.2.0), applies config.toml (green #00CC44 @ brightness 200) over BLE, connection held for full poll cycles

### Key discoveries
1. **btleplug 0.12 WinRT bug**: No `MaintainConnection=true` on GattSession means connection drops when idle. Workaround: directly invoke WinRT `GattSession::FromDeviceIdAsync` + `SetMaintainConnection(true)` after btleplug's connect.
2. **BLE random addresses rotate**: Keyboard advertises with different resolvable random addresses across sessions (seen `2F9F`, `2FA1`, `2FA2`). Scan-based discovery is required — hardcoded address won't work.
3. **Stale paired devices pollute scan**: Windows WinRT returns cached paired-device addresses in scan results even if they're not advertising. Must iterate candidates and try connect on each.
4. **Aggressive BLE sleep**: Keyboard drops BLE advertising within ~30s of idle. Daemon must handle "device not found" as normal and keep polling.
5. **MITM proxy nRF52840 is no longer needed** for control — direct BLE from Windows via btleplug works.

### Files changed
- `src/ble.rs` — MaintainConnection setup, multi-candidate connect, GATT read fallback
- `Cargo.toml` — added `Devices_Bluetooth` + `Devices_Bluetooth_GenericAttributeProfile` windows crate features
- `scripts/ble_direct_control.py` — new bleak validation script

## Next immediate task
- Test: verify lighting changed to green #00CC44 visually
- Test: config.toml reload triggers BLE reapply without reconnect
- Test: disconnect/reconnect when keyboard sleeps and wakes
- Refactor USB + BLE behind a common `JoroDevice` trait to reduce `apply_config_*` duplication
- Unpair keyboard from Windows Bluetooth when done testing (it's currently paired)

## Previous session (2026-04-10 1500–1730) — BLE SET Commands Cracked + Effects Mapped + Rust BLE Module

### Completed
- **SET brightness over BLE — WORKING** — `0x10/0x05 sub1=0x01 data=[brightness]`
- **SET color over BLE — WORKING** — `0x10/0x03 sub1=0x01 data=[01,00,00,01,R,G,B]`
- **RGB color cycling verified** — 3 full R→G→B cycles, visually confirmed on keyboard hardware
- **Firmware version confirmed: v1.2.2.0** (updated from 1.0.4.0)
- **Effect data format decoded** — variable-length: `[effect, param, 0, num_colors, R1,G1,B1, ...]`, dlen = 4 + (num_colors * 3)
- **Static, breathing (1+2 color), spectrum cycling** — all formats captured from Chroma Studio HCI trace
- **Additional driver commands discovered** — SET 0x01/0x02, SET 0x06/0x02 (idle config), more class 0x05 GETs

### Key Discoveries
1. **20-byte padding bug was the "auth gate"** — `central_write_to_keyboard()` padded all writes to 20 bytes. Keyboard requires exact byte lengths (8B for header, 8+N for data). Fixing this made all GETs work on new firmware.
2. **Split write protocol** — SET commands require header and data as TWO SEPARATE ATT Write Requests. Concatenating them into one write returns FAILURE (0x03). Discovered via BT HCI ETW capture of the Razer driver.
3. **sub1=0x01 required** — SET brightness/color need sub-param byte 6 = 0x01. We tried 0x00 and 0x05 — neither worked. Found correct value from HCI capture.
4. **SET color cmd=0x03** (not 0x02) — mirrors GET 0x83 with high bit cleared.
5. **No BLE encryption needed** — SMP pairing fails (PAIR_NOT_ALLOWED) but SET commands work without it. The split write was the only blocker.
6. **Driver init sequence captured** — `0x01/0xA0` (x2), `0x05/0x87`, `0x05/0x84`, `0x05/0x07` SET

### Methods Used
- **BT HCI ETW capture** — `logman` with BTHPORT provider, parsed ETL→XML with `tracerpt`, extracted ATT Write Requests showing split write pattern
- **MITM proxy firmware iterations** — 6 builds testing different hypotheses (padding fix, exact lengths, Write Request vs WwoR, SMP pairing, split writes)
- **Razer driver analysis** — strings extraction from `RzDev_02ce.sys` (67KB, KMDF)

### Working BLE Protocol30 Command Reference
```
GET (single write, 8 bytes):
  [txn, 0, 0, 0, class, cmd, sub1, sub2]

SET (split write: 8-byte header + N-byte data as separate ATT writes):
  Write 1: [txn, dlen, 0, 0, class, cmd, sub1, sub2]
  Write 2: [data bytes...]

Brightness: class=0x10, GET=0x85, SET=0x05, sub1=0x01
Color:      class=0x10, GET=0x83, SET=0x03, sub1=0x01
  Color data: [01, 00, 00, 01, R, G, B] (7 bytes, static effect)
```

## Next immediate task
- Build Python BLE control script using bleak — direct keyboard control without proxy
- **Debug btleplug connection on Windows** — scan doesn't find already-paired devices; first run found+failed to connect, second run didn't find at all. Need to handle Windows paired-device enumeration.
- Test BLE daemon end-to-end (color change via config.toml over BLE)
- Capture wave/reactive/starlight effects from Chroma Studio
- Python bleak direct control script (alternative to btleplug debugging)

## Blockers
- Wave (0x04), reactive (0x05), starlight (0x06) data formats not yet captured
- Brightness change was protocol-confirmed but not visually observed — need re-test

## Key decisions
- Split write protocol is the fundamental mechanism for all BLE SET commands
- No encryption/auth needed — simplifies the control path significantly
- sub1=0x01 appears to be a "profile" or "target" identifier, not VARSTORE
- BLE uses class 0x10 (BLE-native) instead of USB class 0x0F — cmd IDs mirror USB (GET=0x80+, SET=0x00+)
- Effect data is variable-length: dlen = 4 + (num_colors * 3). Static/breathing-1=7B, breathing-2=10B, spectrum=4B
- **BLE key remaps confirmed host-side only** — HCI capture showed zero class 0x02 commands from Synapse; killing Synapse broke the remap instantly. Our WH_KEYBOARD_LL approach is the correct replacement.

## Previous session (2026-04-10 0330–1456) — BLE MITM Proxy + Protocol30 Discovery + FW Update

### Completed
- **Zephyr SDK 0.17.0 installed** — ARM toolchain at `C:\Users\mklod\zephyr-sdk-0.17.0\`
- **Zephyr workspace** — `C:\Users\mklod\zephyrproject\` with west init v4.1.0 + west update
- **CMake 4.3.1, Ninja 1.13.2** installed via winget
- **Zephyr Python deps** installed from `scripts/requirements.txt`
- **BLE MITM proxy firmware** — 4-file Zephyr app (`central.c`, `peripheral.c`, `relay.c`, `main.c`)
  - Builds for `nrf52840dongle/nrf52840` board target
  - Source at `firmware/ble-mitm-proxy/`, build copy at `C:\Users\mklod\zephyrproject\ble-mitm-proxy\`
  - Flash via: `nrfutil device program --firmware .../zephyr.hex --options chip_erase_mode=ERASE_ALL --traits jlink`
- **Upstream (proxy→keyboard) WORKING:**
  - Proxy scans, finds Joro by name, connects as BLE central
  - GATT discovery succeeds: 1524 (write, handle=69), 1525 (notify, handle=71), 1526 (notify, handle=74)
  - Subscribed to notifications on 1525 and 1526
  - Received unsolicited notification: `01 00 00 00 00 00 00 03 2a 65 10 14 47 a7 50 31 e5 f5 60 dd`
  - Byte 7 = 0x03 = "Command Failure" (initially misread as "not authenticated")
  - Bytes 8-19 = 12-byte session/state data (constant per session, changes between sessions)
  - Keyboard stays connected (bonded to proxy MAC, no 2s disconnect)
- **Downstream (Synapse→proxy) PARTIALLY WORKING:**
  - Windows pairs with proxy via BLE (SMP enabled, DIS with VID=0x068E/PID=0x02CE)
  - Synapse detects proxy as "Joro" in device list
  - Windows BLE stack connects/disconnects rapidly (0x13 Remote User Terminated)
  - Synapse never writes to the Razer custom GATT service (1524)
  - Likely missing: HID-over-GATT service — Synapse may require HID before using custom service
- **USB CDC serial logging** on COM12 + SEGGER RTT via J-Link for debug output
- **Bond persistence** via NVS flash storage (`CONFIG_BT_SETTINGS=y`)

### Key Discoveries
- **nRF52840 dongle flash requires FLASH_LOAD_OFFSET=0x0** — board default is 0x1000 (expects MBR bootloader). Without MBR, vector table is empty → HardFault
- **Immediate-mode logging causes stack overflow** in BT RX thread — must use `CONFIG_LOG_MODE_DEFERRED=y`
- **BT RX stack needs 8192 bytes** for BLE central+peripheral with GATT discovery
- **Keyboard BLE pairing slots are exclusive** — 3 slots, only ONE active at a time (OR, not AND). Long-press (5s) clears bond on slot, short-press reconnects. Each slot uses a different MAC (2F:9F, 2F:A2, 2F:A3, 2F:A4, 2F:A5 seen)
- **Keyboard stays connected after bonding** — previous 2s disconnect was due to connecting to already-bonded slots. Fresh slot = stable connection
- **First BLE protocol data captured:** `01 00 00 00 00 00 00 03 ...` on characteristic 1525 — status 0x03 = Command Failure
- **Protocol30 has NO encryption** — Synapse JS source confirms plaintext protocol. 0x03 responses were due to malformed packets, not auth failure

### Build Infrastructure
- Zephyr SDK 0.17.0 at `C:\Users\mklod\zephyr-sdk-0.17.0\`
- Zephyr workspace at `C:\Users\mklod\zephyrproject\` (v4.1.0)
- Build command: `west build -b nrf52840dongle/nrf52840 ble-mitm-proxy --build-dir ble-mitm-proxy/build`
- Flash command: `nrfutil device program --firmware .../zephyr.hex --options chip_erase_mode=ERASE_ALL --traits jlink`
- Must set: `ZEPHYR_SDK_INSTALL_DIR`, `ZEPHYR_BASE`, cmake in PATH

### Protocol30 Discovery (from Synapse source decompilation)
- **Synapse is Electron app** — web modules cached in Service Worker CacheStorage
- **Source found:** `6886.d85cef2c.chunk.js` = `rzDevice30Layla` class (keyboard BLE handler)
- **Packet format:**
  ```
  Byte 0: transactionId (auto-increment)
  Bytes 1-3: data length (24-bit: [0, hi, lo])
  Byte 4: commandClass1
  Byte 5: commandClass2
  Byte 6: commandId
  Byte 7: subCommandId
  Bytes 8+: data payload
  ```
- **Response byte 7 status codes:** 1=BUSY, 2=SUCCESS, 3=FAILURE, 4=TIMEOUT, 5=NOT_SUPPORTED
- **No crypto/auth in JS layer** — Protocol30 is plaintext, the 0x03 status = "Command Failure"
- **Initial probe results with raw bytes:** keyboard echoes byte 0, always returns status 0x03 + 12-byte nonce
- **Protocol30 formatted probe:** no responses received (keyboard had disconnected by then)
- **Key files in Synapse source:**
  - `electron/modules/noble/constants.js` — UUIDs, protocol constants
  - `electron/modules/noble/strategy/protocol30.js` — transport layer
  - `electron/modules/noble/index.js` — BLE connection manager, status code decoder
  - Service Worker cache `6886.d85cef2c.chunk.js` — `rzDevice30Layla` keyboard implementation

### BLE Command Verification (8-byte header probes)
- **GET firmware, battery, device type, power, brightness — all SUCCESS**
- **GET serial, keymap — NOT_SUPPORTED over BLE**
- **All SET commands with data payloads — FAILURE** (command recognized but data format wrong)
- **8-byte header-only = correct format for GET; data payloads need different byte layout than USB**
- **Connecting to proxy from Python:** use `BleakClient(addr)` directly (no scan) since device is paired

### SET Command Testing — Data Payload Issue (pre-FW-update)
- **Header-only (8B) commands worked** — GET firmware, battery, device type = SUCCESS
- **Any command with data (>8B) returned FAILURE (0x03)** regardless of class/cmd/data content
- Tested: BLE-native class IDs (0x10/0x05), USB class IDs (0x0F/0x04), single write, split write, 20B padded, session token echo — ALL fail
- Synapse uses kernel driver (`RzDev_02ce.sys`) + `rzLampArrayChannel` for lighting, not Protocol30 JS

### Firmware Update
- Updated Joro firmware via Razer updater (USB required)
- **DFU PID = `0x110E`** (VID `0x1532`) captured during update
- **New firmware enables BLE lighting in Synapse** — confirmed color changes + on/off toggle work via Synapse over BLE
- **New firmware locks ALL Protocol30 commands behind authentication** — even GET firmware (which worked on old FW) now returns FAILURE (0x03)
- Response suffix changed from `2a e5 10 14 67 a7 71 31 ed f5 60 d9` (old FW session data) to `ff ff ff ff ff ff ff ff ff ff ff ff` (new FW = no session)
- The Razer driver handles session authentication transparently — JS layer never sees it

### Synapse Architecture Discovery
- **Lighting uses `rzLampArrayChannel`** → Windows LampArray API → `RzDev_02ce.sys` kernel driver
- **Driver constructs Protocol30 commands** internally — Synapse JS never builds lighting packets directly
- **Two communication paths:** Protocol30 direct (battery, firmware) vs driver-mediated (lighting, keymaps)
- **Product ID 717** = Joro in Synapse; `project_anne_joro` webpack module
- **Driver file:** `RzDev_02ce.sys` (67KB) handles BLE Protocol30 for lighting/keymaps

## Next immediate task
- **Capture Razer driver's session auth handshake** — the driver (`RzDev_02ce.sys`) authenticates with the keyboard before sending commands. Must capture this:
  - **Option A:** Add HID-over-GATT stub to MITM proxy so driver recognizes it as Joro, connects through it, and we capture the full auth + command sequence
  - **Option B:** Reverse-engineer `RzDev_02ce.sys` with IDA/Ghidra to find the auth algorithm
  - **Option C:** Fix USBPcap on this machine and capture what the driver sends over BLE at the HCI level
- **New firmware version needs to be queried** — GET firmware now fails too, need auth first

## Blockers
- New firmware requires session authentication for ALL Protocol30 commands (including GETs)
- Razer driver handles auth transparently — not visible in Synapse JS layer
- USBPcap broken on this machine — can't passively capture BLE HCI traffic

## Blockers
- Synapse won't use the custom GATT service without (likely) HID-over-GATT present
- `chip_erase_mode=ERASE_ALL` wipes bond storage — need to re-pair after every flash. Consider using `ERASE_RANGES_NEEDED_BY_FIRMWARE` instead

## Key decisions
- `CONFIG_FLASH_LOAD_OFFSET=0x0` — no bootloader, flash via SWD directly
- `CONFIG_LOG_MODE_DEFERRED=y` — prevents stack overflow in BT callbacks
- `CONFIG_BT_MAX_CONN=3` — upstream + downstream + spare for reconnect churn
- Bond persistence via NVS (`CONFIG_BT_SETTINGS=y`)
- Source on L: drive, build on C: drive (west can't handle cross-drive paths)

## Previous session (2026-04-10 0330) — Zephyr SDK 0.17.0 Install (Windows, nRF52840)

### Completed
- **SDK version confirmed:** Zephyr v4.1.0 compatible with SDK 0.17.0 (released 2024-10-20)
- **Downloaded:** minimal bundle (`zephyr-sdk-0.17.0_windows-x86_64_minimal.7z`) + arm toolchain (`toolchain_windows-x86_64_arm-zephyr-eabi.7z`) from GitHub sdk-ng releases
- **Extracted to:** `C:\Users\mklod\zephyr-sdk-0.17.0\` — contains `arm-zephyr-eabi/`, `cmake/`, `sdk_version`, `sdk_toolchains`, `setup.cmd`
- **Toolchain verified:** `arm-zephyr-eabi-gcc.exe (Zephyr SDK 0.17.0) 12.2.0` runs correctly
- **CMake package registered:** `cmake -P cmake/zephyr_sdk_export.cmake` wrote registry key `HKCU\Software\Kitware\CMake\Packages\Zephyr-sdk` — CMake `find_package(Zephyr-sdk)` will auto-locate the SDK without needing `ZEPHYR_SDK_INSTALL_DIR`
- **Env var set:** `ZEPHYR_SDK_INSTALL_DIR=C:\Users\mklod\zephyr-sdk-0.17.0` as persistent user environment variable (backup for tools that need it explicitly)
- **Toolchain used:** 7zr.exe (from 7-zip.org v26.00) for extraction — Git bash has no 7z, and 7-Zip is not installed system-wide

### Key Info
- SDK install path: `C:\Users\mklod\zephyr-sdk-0.17.0\`
- GCC: `arm-zephyr-eabi-gcc` 12.2.0 at `C:\Users\mklod\zephyr-sdk-0.17.0\arm-zephyr-eabi\bin\`
- Zephyr workspace: `C:\Users\mklod\zephyrproject\` (west init --mr v4.1.0)
- CMake: `C:\Program Files\CMake\bin\cmake.exe` v4.3.1

---

## Last session (2026-04-10 0300) — BLE Sniffing Setup + GATT Enumeration

### Completed
- **nRF52840 dongle setup:** Installed nrfutil, ble-sniffer command, Wireshark extcap bootstrap
- **Dongle recovery:** Previous custom firmware had overwritten bootloader. Restored DFU bootloader via J-Link (SWD) + `open_bootloader_usb_mbr_pca10059_debug.hex`, then flashed sniffer firmware via DFU
- **J-Link driver fix:** SEGGER J-Link V9.34a installed, USB driver manually pointed at `C:\Program Files\SEGGER\JLink_V934a\USBDriver`
- **Barrot BLE 5.4 adapter:** Installed driver v17.55.18.936 from MS Update Catalog (extracted .cab, `pnputil -a` + Device Manager manual update)
- **BLE sniffer captures:** 8+ attempts. Captured Joro advertising and CONNECT_IND packets but sniffer marks them as malformed — cannot follow connection onto data channels. Single-radio sniffer insufficient.
- **GATT service enumeration:** Full map via WinRT Python (see `docs/ble-reverse-engineering.md`)
- **Razer BLE protocol discovery:** Custom service `5240xxxx` does NOT use USB 90-byte packet format. MTU=23 (20-byte payload max). Characteristics contain encrypted-looking data. Protocol requires authentication/session setup.
- **BLE command testing:** 20-byte writes accepted by `...1524` char but no valid command response. 90-byte writes fail (MTU too small). USB protocol does not transfer to BLE.

### Key Discoveries
- **BLE protocol is separate from USB** — different packet format, likely encrypted proprietary channel
- **Keyboard works over BLE without Synapse** — standard HID-over-GATT handles input natively
- **Joro BLE MAC rotates** on each pairing (seen a0, a1, a2, a3 suffixes)
- **Barrot BLE 5.4 adapter** works but doesn't negotiate MTU above 23
- **Intel onboard BT** in Error state in Device Manager, unusable

### Tools Installed This Session
- nrfutil v8.1.1 at `C:\Users\mklod\bin\nrfutil.exe`
- nrfutil ble-sniffer v0.18.0 + nrfutil device
- SEGGER J-Link V9.34a at `C:\Program Files\SEGGER\JLink_V934a\`
- Barrot BLE 5.4 driver v17.55.18.936
- Python bleak package

### Scripts Created
- `scripts/ble_gatt_enum.py` — enumerate Joro GATT services via WinRT
- `scripts/ble_test_command.py` — BLE command testing (failed due to protocol mismatch)

## Next immediate task
- **BLE MITM proxy** — flash nRF52840 with GATT proxy firmware to intercept Synapse↔Joro BLE traffic in plaintext. Option 1 (sniffer) failed; Option 2 (MITM) is the path forward.
- See `docs/ble-reverse-engineering.md` for full plan

## Blockers
- BLE protocol unknown — need MITM capture of Synapse session to reverse-engineer
- Intel BT adapter broken (not critical, Barrot works)

## Key decisions
- Single-radio BLE sniffer cannot reliably capture connection traffic — MITM proxy required
- BLE custom service uses different protocol than USB (not just MTU-limited)
- Barrot BLE 5.4 adapter is primary BT adapter going forward

---

## Previous session (2026-04-10 0230) — Autostart + Persistent Storage Investigation

### Completed
- **Autostart toggle** — tray menu "Autostart: On/Off", writes `HKCU\...\Run\JoroDaemon` registry key
- **Persistent remap storage investigation** — CONCLUDED: not available for arbitrary keys
  - Probed all class 0x02 SET candidates (0x02, 0x03, 0x07, 0x0D, 0x28) with size=0 after volatile keymap write — none made remap survive replug
  - Varstore prefix (0x01 byte before entry) — firmware didn't recognize format
  - Probed classes 0x03, 0x04, 0x05 GET commands — no storage/save commands found
  - Class 0x04 has 48 GET commands (0x80-0xAF) all returning size=0 — possibly empty macro/profile slots
- **Lighting persistence confirmed** — SET 0x0F/0x02 (static color) auto-persists across USB replug. Firmware stores lighting state permanently without explicit save command.
- **Python USB transport broken for keymap writes** — pyusb ctrl_transfer no longer writes keymaps after replug. Rust daemon (rusb with claim_interface) still works. Root cause unclear.

### Key Discoveries
- **Lighting: auto-persistent.** Color/brightness survive replug. Class 0x0F writes to non-volatile storage automatically.
- **Keymaps: always volatile.** Class 0x02/0x0F writes reset on replug. No save command found. Daemon must re-apply on every connect (which it already does).
- **F-key persistent remaps (Synapse):** likely use a separate firmware mechanism for the multimedia/Fn layer, not the general keymap API. Or Synapse re-applies on startup like our daemon.
- **Fn key row defaults to multimedia** (volume, mute, etc). Fn modifier enables actual F-keys. Fn hold makes backlight solid white.

## Next immediate task
- Test 2.4GHz dongle (PID 0x02CE)
- Release build + single exe packaging
- Map more keyboard matrix indices

## Blockers
- None

## Key decisions
- Persistent keymap storage not available — daemon re-applies on connect (correct approach)
- Autostart via registry Run key (not Startup folder)

---

## Previous session (2026-04-10 0130) — Modifier Gate: Both Remaps Working

### Completed
- **Complete rewrite of hook architecture** — replaced old pending-modifier state machine with "modifier gate" approach
- **Lock key → Delete: WORKING** (single tap + hold-to-repeat)
- **Copilot key → Ctrl+F12: WORKING** (triggers Greenshot screenshot)
- **Win key, Win+E, Start menu all work normally** — gate resolves on next keypress, non-trigger keys replay immediately

### Architecture: Modifier Gate
- On gate modifier keydown (Win): suppress it, wait for next key
- Trigger key (L, 0x86): fire remap, enter active trigger state
- Non-trigger key (E, D, ...): replay gate mod immediately, pass key through
- Gate mod key-up (Win tap): replay full tap → Start menu works
- Two-phase release: active trigger stays alive until BOTH trigger↑ and gate↑ arrive
- Auto-repeat: gate mod repeats suppressed, trigger repeats → output key repeats
- Cleanup: inject gate mod key-up on completion (LRESULT(1) suppression doesn't fully clear Windows internal key state)
- Prefix mods: Copilot sends LShift↓ before LWin↓; LShift leaks through pre-gate, cancelled with injected key-up when trigger fires

### Bugs Found & Fixed
1. **Orphan key-ups**: firmware sends Win↑ before L↑ (not L↑ before Win↑ as assumed). Two-phase release needed.
2. **Auto-repeat Win↓ leaked through gate**: during hold-to-repeat, Win↓ repeats fell through to gate logic, got replayed as injected Win↓ without matching Win↑. Fixed by suppressing gate_vk repeats in active trigger.
3. **LRESULT(1) doesn't fully clear Windows key state**: suppressing Win↓ and Win↑ left Windows thinking Win was stuck. Fixed by injecting cleanup Win↑ on completion.
4. **Copilot LShift prefix leaked**: LShift↓ arrives before LWin↓ (gate mod), passed through uncaught. Fixed by injecting LShift↑ when trigger fires.
5. **Injected keys don't prevent Start menu**: Windows ignores LLKHF_INJECTED events for Start menu detection. Dummy key approach (scancode 0xFF) failed.

### Key Discovery
- **Windows Start menu detection ignores injected events** — only hardware (non-LLKHF_INJECTED) keys between Win↓ and Win↑ prevent Start menu from triggering. Must suppress Win↓ entirely, not try to cancel it after the fact.
- **Firmware macro event ordering**: Lock key sends Win↓, L↓, but key-ups arrive as Win↑ THEN L↑ (not L↑ then Win↑). SendInput during hook processing may also reorder events.

## Next immediate task
- Commit the working state
- Disable debug logging infrastructure (file logger can be removed or kept behind flag)
- Test 2.4GHz dongle (PID 0x02CE)
- Add Windows autostart registration
- Release build + single exe packaging

## Blockers
- None

## Key decisions
- Modifier gate > trigger-based interception > pending-modifier state machine (each approach failed and informed the next)
- Debug logging to file (`hook_debug.log`) instead of stderr to avoid hook timeout removal
- `DisableLockWorkstation` registry key required for Lock→Delete
- Prefix mods (LShift for Copilot) cancelled at trigger time, not pre-gate (can't know if gate will follow)

---

## Previous session (2026-04-09 2351) — Combo-Source Remaps: Lock Works, Copilot Blocked

### Completed
- **Lock key → Delete: WORKING.** Requires `DisableLockWorkstation` registry key (Win+L is OS-protected, can't be intercepted by WH_KEYBOARD_LL or RegisterHotKey).
  - Registry: `HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System\DisableLockWorkstation = 1`
- **Unified pending-modifier state machine** — replaced hardcoded `COMPANION_MODIFIERS` with data-driven `PendingModifierRemap` table supporting multiple triggers per held modifier
- **Multi-candidate trigger matching** — when LWin is pressed, waits for EITHER L (Lock) or 0x86 (Copilot)
- **Scan codes in SendInput** — `MapVirtualKeyW` populates `wScan` field
- **Config format** — `from = "Win+L"` combo-source syntax works in TOML
- 34 unit tests pass (4 new for combo-source parsing)
- 4 commits: scan codes, unified state machine, build_remap_tables, wiring

### IN PROGRESS: Copilot key → Ctrl+F12
- **Root cause is NOT SendInput modifier injection** — CapsLock→Ctrl+F12 uses the exact same SendInput approach and works perfectly. The proven ComboRemap path (single key → combo output) is reliable.
- **Actual root cause: Copilot key's LShift prefix leaks through.** Copilot sends LShift↓, LWin↓, 0x86↓. LShift passes through to the system BEFORE LWin enters the pending state. This leaked LShift combines with our injected Ctrl+F12, creating Shift+Ctrl+F12 and corrupting modifier state.
- **Secondary issue: key-up ordering.** Copilot releases LWin↑ before 0x86↑ (opposite from Lock key's L↑ then LWin↑). Earlier code assumed trigger↑ before held↑.
- Approaches tried:
  1. **Companion modifier state machine** — single trigger per held VK. Failed: only matched first table entry when multiple remaps share held_vk. Fixed with multi-candidate.
  2. **RegisterHotKey** — Win+L fails (OS-protected). Win+0x86 conflicts with hook (double fire).
  3. **release_stray_modifiers via GetAsyncKeyState** — injected unpaired modifier key-ups that accumulated.
  4. **Tap-style output** — correct direction but LShift leak still corrupts state.
- **LShift↑ injection on pending entry** — tried injecting LShift↑ when LWin enters pending. Still caused stuck modifiers. The held-style combo (combo_down on confirm, combo_up on first key-up) also failed.
- **Next approach:** Find Copilot firmware matrix index → remap to single key (F13) at hardware level → use proven ComboRemap path (F13→Ctrl+F12, same as working CapsLock remap). This completely avoids the LShift+LWin prefix problem.

### Key Protocol Discoveries This Session
- **Copilot key actual sequence:** LShift↓, LWin↓, VK 0x86↓, VK 0x86↑, LWin↑, LShift↑ (scan=0x006E)
- **Lock key sequence:** LWin↓, L↓, L↑, LWin↑ (standard firmware macro)
- **Win+L is OS-protected:** Cannot be intercepted by WH_KEYBOARD_LL (hook sees events but LRESULT(1) doesn't prevent lock). Cannot be claimed by RegisterHotKey. Only `DisableLockWorkstation` registry works.
- **WH_KEYBOARD_LL hook fragility:** Windows removes hooks that don't respond within ~300ms (LowLevelHooksTimeout). Per-key debug logging (eprintln + Mutex locks) may trigger this.

### Safety Lessons
- **MUST have in-daemon watchdog** that periodically releases all modifiers if no key activity for N seconds — auto-exit timeout doesn't help when keyboard is stuck and can't type "kill"
- **Never inject unpaired modifier events** — GetAsyncKeyState + inject key-up is unreliable with injected events
- **Test with safety net first** — remote control access should be set up BEFORE testing keyboard hooks

## Next immediate task
- **Ship Lock→Delete only** — commit current working state, remove Copilot remap from default config
- **Find Copilot matrix index** — run Python key mapper to discover if Copilot key has a firmware-remappable index
- **Add modifier watchdog** — periodic check in daemon that releases all modifiers if they've been held too long without key events
- Test 2.4GHz dongle (PID 0x02CE)

## Blockers
- Copilot→Ctrl+F12 via SendInput modifier injection is fundamentally broken
- Need Copilot key matrix index for firmware-level approach

## Key decisions
- `DisableLockWorkstation` registry key required for Lock→Delete
- SendInput modifier injection (Ctrl↓/Ctrl↑) corrupts Windows keyboard state — avoid for any remap that outputs modifier combos
- Single-key output remaps (like Delete) work reliably via SendInput
- CapsLock→Ctrl+F12 worked in earlier session because CapsLock has no preceding modifiers — the Copilot key's LShift+LWin prefix is what makes it intractable

---

## Previous session (2026-04-09 late) — Rust Daemon MVP + Key Identification

### Completed
- Built full Rust systray daemon (`joro-daemon`) — 6 source modules, 30 unit tests
- **Hardware verified:** lighting (static color + brightness), firmware query, host-side combo remaps
- CapsLock -> Ctrl+F12 confirmed working via WH_KEYBOARD_LL keyboard hook
- Auto-reconnect on USB replug, config file auto-reload on change
- Systray icon with connected/disconnected state, right-click menu (reload, open config, quit)
- Fixed: USB interface claiming, hidden winit window for event loop, KEYEVENTF_EXTENDEDKEY only for extended keys
- Config at `%APPDATA%\razer-joro\config.toml`

### Key Identification
- **Copilot key** = sends LWin (0x5B) + VK 0x86. NOT RWin as initially assumed.
  - Companion modifier state machine: holds LWin, waits for 0x86, suppresses both
  - Intercept works (search no longer opens), but SendInput combo not reaching apps — TODO
- **Lock key** = firmware macro sending LWin+L. Indistinguishable from manual Win+L. Not remappable without intercepting all Win+L.
- **Persistent remaps:** F-key row remaps persist on-device (Synapse-written). Modifier/special key remaps are volatile. Fn row defaults to multimedia keys (F4=arrange, F5=mute, F6=vol-, F7=vol+).

### Build Infrastructure
- Rust 1.94.1 + MSVC 14.44, VS Build Tools 2022
- `build.ps1` wrapper for MSVC env setup
- Target dir: `C:\Users\mklod\AppData\Local\razer-joro-target` (local, not SMB)
- `.cargo/config.toml` excluded from git (machine-specific)

---

## Previous session (2026-04-09 evening) — Keymap Validation + BLE Exploration

### Completed
- **CapsLock matrix index identified: 30** — confirmed via remap-to-F12 test
- **Modifier combo remap testing** — exhaustively tested all entry format variations:
  - Simple key-to-key remap: WORKS (type=02/02, any HID usage including modifiers)
  - CapsLock -> LCtrl: WORKS (usage=0xE0)
  - Modifier+key combos (e.g., Ctrl+Esc): NOT SUPPORTED by firmware
  - Tested: modifier in pad byte, extra bytes, type fields (01/02, 02/01, 03/02, 07/02, 02/07, 02/03), two-entry writes, modifier usage in pad — none produce combos
- **Conclusion:** Firmware only does 1:1 key swaps. Modifier combos must be implemented in host software (intercept + synthesize).
- Created `proto/validate_keymap.py` and `proto/find_capslock_v2.py`
- Scanned all class 0x02 GET/SET commands:
  - GET: 0x82, 0x83, 0x87(2b), 0x8D(5b), 0x8F(keymap), 0xA4(returns 0x02...), 0xA8
  - SET: 0x02, 0x03, 0x07, 0x0D, 0x0F(keymap), 0x24(status=0x03), 0x28

### Key Protocol Discovery
- **Keymap entry format:** `[idx, 0x02, 0x02, 0x00, hid_usage, 0x00, 0x00, 0x00]`
  - type1/type2/pad/extra fields are ignored by firmware — only idx and usage matter
  - Modifier keys (0xE0-0xE7) work as single-key remaps
  - No combo/macro support in firmware keymap entries
- **GET keymap (0x02/0x8F):** Always returns first 8 entries only, pagination broken/unsupported
- **Known matrix indices:** 1=Grave, 2-8=digits 1-7, 30=CapsLock

### BLE Exploration
- **BLE address:** C8:E2:77:5D:2F:9F
- **BLE VID/PID:** 0x068E / 0x02CE (different from USB 0x1532/0x02CD!)
- **Connection params:** interval 7.5-15ms, slave_latency=20, supervision_timeout=3.0s
- **Max PDU:** 23 bytes (20-byte max GATT write payload)
- **GATT services:**
  - Generic Access, Generic Attribute, Device Info, Battery (100%)
  - HID over GATT (0x1812) — access denied (Windows locks this)
  - **Custom Razer service: `52401523-f97c-7f90-0e7f-6c6f4e36db1c`**
    - `...1524` — write (TX: command channel)
    - `...1525` — read+notify (RX: response channel, 20 bytes)
    - `...1526` — read+notify (RX2: secondary channel, 8 bytes)
- **BLE command protocol:** NOT same as USB. Device accepts 20-byte writes but returns
  `[echo_byte0, 00*6, 03, <12-byte nonce>]` — byte 7=0x03 likely means "not authenticated"
- **Conclusion:** Razer BLE protocol requires authentication handshake before commands.
  Synapse handles this. Reversing would need BLE traffic capture from Synapse session.
- **Sleep fix approach:** `maintain_connection=False` in WinRT GattSession — setting to True
  may reduce reconnect delay. Also, supervision_timeout=3s could be tuned.

---

## Previous session (2026-04-09) — Tasks 1-6 (partial) + Protocol RE

### Completed
- Created full Python prototype: `razer_packet.py`, `usb_transport.py`, `commands.py`, `test_lighting.py`, `enumerate.py`
- Cloned openrazer PR branch to `openrazer-ref/`
- Installed Wireshark + USBPcap (USBPcap device nodes broken on this system, not needed)
- **Verified lighting control on hardware** — static color + brightness confirmed
- **Brute-force command scan** — discovered all supported command class/id pairs
- **Key remapping confirmed working** — SET 0x02/0x0F changes keymap, verified backtick->A remap on hardware
- CapsLock index search in progress (confirmed in range 20-35, currently mapped to unique F-keys for identification)

### Major Protocol Discoveries
1. **Transport:** pyusb raw USB control transfers required (hidapi feature reports return 0x05)
   - SET_REPORT: bmRequestType=0x21, bRequest=0x09, wValue=0x0300, wIndex=0x03
   - GET_REPORT: bmRequestType=0xA1, bRequest=0x01, wValue=0x0300, wIndex=0x03
2. **Packet struct:** openrazer `razer_report` layout
   - status@0, txn_id@1(0x1F), remaining_pkts@2-3, proto@4, data_size@5, class@6, cmd@7, args@8-87, crc@88, reserved@89
3. **LED constants:** VARSTORE=0x01, BACKLIGHT_LED=0x05
4. **Lighting commands (verified on hardware):**
   - Static color: class=0x0F id=0x02 size=9 args=[01,05,01,00,00,01,R,G,B]
   - Set brightness: class=0x0F id=0x04 size=3 args=[01,05,brightness]
   - Get brightness: class=0x0F id=0x84 size=1 args=[01] -> resp [01,05,brightness]
   - Get firmware: class=0x00 id=0x81 size=0 -> resp [major,minor]
5. **Key remapping (verified on hardware):**
   - GET keymap: class=0x02 cmd=0x8F — returns 10-byte header + 8x8-byte entries per page
   - SET keymap: class=0x02 cmd=0x0F — write entries, format: `[idx, 0x02, 0x02, 0x00, target_usage, 0x00, 0x00, 0x00]`
   - Entry index is keyboard matrix position (NOT HID usage ID)
   - Known indices: 1=backtick, 2-8=digit keys 1-7
   - After SET, no GET_REPORT response (IO error) — must send-only, then read separately
   - USB replug resets all remaps to defaults
6. **Idle/sleep config (discovered, not yet tested):**
   - GET: class=0x06 cmd=0x86 -> [00,00,00,64,00,00,00,00,00,04,A0,00] (0x64=100, 0x04A0=1184)
   - GET extended: class=0x06 cmd=0x8E -> 14 bytes with duplicate timing values
7. **Full supported command map:** classes 0x00,0x02,0x03,0x04,0x05,0x06,0x07,0x0A,0x0F all have supported commands

### Venv & Environment
- Python venv: `C:/Users/mklod/AppData/Local/razer-joro-venv` (not on SMB share)
- Must kill `razer_elevation_service.exe` before USB access
- libusb DLL: bundled via `pip install libusb`
- Deps: hidapi, bleak, pyusb, libusb

## Last session (2026-04-09) — Task 1: Rust scaffold

### Completed
- Created `Cargo.toml` with all deps: rusb, tray-icon, winit, windows, toml, serde, image
- Created `src/main.rs` + empty module stubs: usb, config, remap, keys, tray
- Created `config.example.toml`
- Installed Rust (rustc 1.94.1, MSVC toolchain) — was not previously installed
- Installed VS Build Tools 2022 with C++ workload (MSVC 14.44.35207, WinSDK 10.0.26100.0)
- Resolved two build blockers:
  1. Git's `link.exe` shadowing MSVC's — fixed via explicit linker path in `.cargo/config.toml`
  2. SMB source drive blocks build script execution — fixed via `build.target-dir` pointing to local AppData
- Created `build.ps1` wrapper script that sets MSVC/WinSDK env vars for clean builds
- Created `.cargo/config.toml.example` (machine-specific config excluded from git)
- `cargo build` verified: Finished in ~37s, 0 errors
- Committed as `feat: scaffold joro-daemon Rust project`

### Key decisions
- `.cargo/config.toml` excluded from git (machine-specific paths); `.cargo/config.toml.example` committed instead
- Build target dir: `C:\Users\mklod\AppData\Local\razer-joro-target` (local, not on SMB)
- Build wrapper: use `.\build.ps1` instead of raw `cargo build`

## Last session (2026-04-09) — Task 2: Key lookup tables

### Completed
- Implemented `src/keys.rs` — full VK/HID lookup tables using `std::sync::LazyLock` HashMaps
- Covers: A-Z, 0-9, F1-F12, navigation keys, punctuation, modifiers (L/R), App key
- Functions: `key_name_to_vk()`, `key_name_to_hid()`, `parse_key_combo()`, `parse_single_hid_key()`
- All 15 specified tests pass (TDD: tests written first, then implementation)
- Committed: `feat: key lookup tables — VK codes, HID usages, combo parsing`

## Last session (2026-04-09) — Tasks 3 & 4: Packet builder/parser + USB device communication

### Completed
- Implemented `src/usb.rs` — full Razer packet builder, parser, and RazerDevice USB API
- **Task 3: Packet builder/parser (TDD)**
  - `build_packet()` — constructs 90-byte Razer packet with CRC (XOR bytes 2..87)
  - `parse_packet()` — decodes response packet, validates CRC
  - `ParsedPacket` struct with status, transaction_id, data_size, command_class, command_id, args, crc_valid
  - All 9 unit tests pass (test_build_packet_size, header, args, crc, parse_roundtrip, bad_crc, get_firmware, set_static_color, keymap_entry)
  - Committed: `feat: Razer packet builder and parser with CRC`
- **Task 4: USB device communication**
  - `RazerDevice::open()` — scans all USB devices for Joro WIRED (0x02CD) and DONGLE (0x02CE) PIDs
  - `send_receive()` — SET_REPORT control transfer + 20ms sleep + GET_REPORT
  - `send_only()` — SET_REPORT + 20ms sleep (for keymap SET which has no GET_REPORT response)
  - `get_firmware()`, `set_static_color()`, `set_brightness()`, `set_keymap_entry()`, `is_connected()`
  - `ConnectionType` enum (Wired/Dongle)
  - Verified compiles clean (warnings only, no errors)
  - Committed: `feat: USB device communication — open, send/receive, lighting, keymap`

## Last session (2026-04-09) — Tasks 6, 7, 8: Host hook, Tray, Main loop

### Completed
- **Task 6: `src/remap.rs`** — WH_KEYBOARD_LL hook engine
  - `build_remap_table()` — filters config remaps; host-side only for combos with `+`
  - `install_hook()` / `remove_hook()` — WH_KEYBOARD_LL via `SetWindowsHookExW`
  - `hook_proc` — injected-event guard (LLKHF_INJECTED bit 0x10), suppresses mapped keys
  - `send_combo_down/up()` — builds INPUT array (modifiers then key), calls `SendInput`
  - Workaround: `SendHook` wrapper to make `HHOOK` (raw pointer) `Send`-safe for `Mutex`
  - Compiles clean (warnings only)
- **Task 7: `src/tray.rs`** — systray icon + menu
  - `create_icon()` — programmatic 32x32 RGBA circle (green=connected, grey=not)
  - `JoroTray::new()` — status/firmware (disabled), separator, reload/open-config/quit
  - `JoroTray::set_connected()` — updates icon + menu text
  - `poll_menu_event()` — non-blocking `try_recv()`
  - Compiles clean
- **Task 8: `src/main.rs`** — full event loop
  - `App` struct: tray, device, config, paths, poll timers
  - `ApplicationHandler` impl: `resumed` (create tray, install hook, connect), `about_to_wait` (2s device poll, 5s config poll, 100ms WaitUntil)
  - `apply_config()` — sets color, brightness, firmware keymaps (skips `+` combos)
  - `check_device()` — polls `is_connected()`, reconnects if lost
  - `check_config_changed()` — mtime compare, auto-reload
  - `handle_menu_events()` — quit/reload/open-config
  - Compiles clean, all 30 tests pass

### One API adaptation needed
- `HHOOK` is `!Send` (wraps `*mut c_void`) — added `SendHook` newtype with `unsafe impl Send` to allow `Mutex<Option<SendHook>>` as a static

## Last session (2026-04-09 night) — Tasks 2+3: Unified pending-modifier state machine

### Completed
- Replaced `COMPANION_MODIFIERS`/`PendingCompanion` hardcoded types with data-driven `PendingModifierRemap` struct and `PendingState` machine
- Added `PENDING_MOD_TABLE` and `PENDING_STATE` statics
- Added `update_pending_mod_table()` public API
- Rewrote `hook_proc` to use table-driven pending-modifier logic (handles any modifier+trigger combo, not just Copilot)
- Added `get_confirmed_trigger()` helper to look up trigger VK from table during key-up handling
- "Confirmed" state uses `trigger_vk = 0` as sentinel — cleaner than a separate bool field
- All 30 tests pass, build clean
- Commit: `fb1f870`

## Last session (2026-04-09) — Tasks 5, 6, 7: Wire up + config defaults + dead code

### Completed
- **Task 5 (main.rs):** Both `resumed()` and `reload_config()` now call `build_remap_tables()` and populate both `REMAP_TABLE` and `PENDING_MOD_TABLE`
- **Task 6 (config.rs):** `DEFAULT_CONFIG` now ships with `Win+L → Delete` and `Win+Copilot → Ctrl+F12` remaps enabled by default; test updated to assert 2 entries
- **Task 6 (live config):** `%APPDATA%\razer-joro\config.toml` updated — replaced old `CapsLock→Ctrl+F12` + `Copilot` (wrong, non-combo form) entries with new `Win+L→Delete` and `Win+Copilot→Ctrl+F12`
- **Task 7 (remap.rs):** Removed `build_remap_table()` compatibility shim (dead code); `COMPANION_MODIFIERS`/`PendingCompanion`/`PENDING_COMPANION` were already gone
- All 34 tests pass, build clean
- Commit: `64c3951`

## Next immediate task
- **Hardware verification:** Run daemon, test Win+L intercept (should → Delete, not lock screen) and Copilot key (should → Ctrl+F12, not Copilot panel)
- **FIX: Copilot key combo output** — previously intercepted correctly but SendInput combo didn't reach apps. New state machine may behave differently — needs re-test.
- Test 2.4GHz dongle (PID 0x02CE) — should work with same USB protocol
- Map more keyboard indices (only 1-8 + 30 known)
- Add Windows autostart registration
- Release build + single exe packaging
- Investigate persistent vs volatile firmware remaps (F-key row persists, others don't)

## Blockers
- None (USBPcap not needed, brute-force scan + direct command testing works)

## Key decisions
- Skipped Wireshark sniffing — brute-force command scan + direct testing more efficient
- pyusb + libusb (not hidapi) for all USB communication
- openrazer PR #2683 as reference for lighting commands
- Razer services must be killed before device access
- Key remap uses matrix index, not HID usage — need index mapping table
- USB replug resets remaps (no onboard storage for custom maps)
- Firmware only supports 1:1 key swaps — combos/macros must be host-side
- CapsLock = matrix index 30

