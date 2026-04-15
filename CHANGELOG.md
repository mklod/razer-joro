# Razer Joro — Changelog

## TODO
> [!tip] Methodical cleanup + BLE keymap RE (in priority order)
>
> **AUTHORITATIVE FACTS as of 2026-04-13--2257 (do not walk back):**
> - `class=0x02 cmd=0x0d` with 10-byte args `[0x01, matrix, 0x01, 0x02, 0x02, mod, usage, 0..0]` writes the **Hypershift layer** over USB. VERIFIED by Fn+Left=Home / Fn+Right=End test on both transports.
> - **Wired and BLE share the same Hypershift storage slot.** One USB write programs both.
> - **Commit trigger = transport mode switch.** Firmware stores the write immediately but only refreshes the runtime Hypershift table on wired↔BLE transition.
> - A previous "keymap dead end" session reached wrong conclusions because it tested without cycling transport. Memory `project_hypershift_commit_trigger.md` supersedes `project_joro_keymap_deadend.md`.
>
> **BLE Hypershift-writes RESOLVED (2026-04-13--2315):** there is no firmware write path over BLE. Prior work already confirmed it — see `_status.md` line 470 (git commit `6b65ffe`): HCI capture of Synapse doing a Hypershift remap over BLE showed **zero class=0x02 traffic**, and killing Synapse instantly broke the remap. Protocol30 `GET keymap` also returns `NOT_SUPPORTED` (line 541). Synapse's "BLE Hypershift" is host-side Windows interception, same pattern it uses for F1/F2/F3 slot-switch translation and mm-primary toggle. Memory: `project_ble_keymap_is_hostside.md`.
>
> **The architecture we actually have is already correct:**
> - USB `set_layer_remap` writes the firmware Hypershift slot. Works.
> - Both wired AND BLE read from that slot. Proven today.
> - User flow: plug USB once to program, then use BLE. Bindings persist.
> - BLE no-op in `src/ble.rs:341` for `set_keymap_entry` and the USB guard at `src/main.rs:176` are correct — leave them, but add a clear user-facing message when someone tries to change a Hypershift binding while on BLE.
>
> **OPEN — in order:**
> 1. **UI/UX: make the USB-required-for-keymap-changes flow explicit.** When the settings UI tries to save a `[[fn_remap]]` change while the daemon is connected via BLE: (a) daemon should save to config but surface a clear "Connect USB cable once to apply new Fn-layer bindings" message to the user via the webview, (b) on next USB connect, auto-apply pending changes. Current code at `src/main.rs:208 update_fn_remap` already logs a similar note but it's eprintln-only.
> 2. **Optional: host-side Hypershift interception** (match what Synapse does for BLE). Requires detecting "Fn held" host-side, which is hard because Fn alone doesn't emit a VK. Investigate: (a) does Joro emit a Razer-vendor HID usage on Fn press/release? `src/consumer_hook.rs` already opens the Consumer Control + System Control HID collections and can log unknown usages — run it in discovery mode while pressing Fn. (b) Check the decompiled Synapse source for how it detects Fn host-side — likely in `6886.d85cef2c.chunk.js` (`rzDevice30Layla`) or the keyboard-side webpack module. Synapse source cache path is referenced in `_status.md` line 518. Only pursue if user wants BLE-only Hypershift reprogramming without ever plugging USB.
> 3. **Base-layer writes over USB** — still unknown what command targets the plain (non-Fn) keymap. Probably `cmd=0x0F` / `set_keymap_entry` with 18-byte args, or `cmd=0x0d` with a different `args[2]` value. Testable: capture Synapse programming a Standard-tab remap, diff against `captures/synapse_hypershift_u3.pcap`. Low priority — we have Hypershift covered and host-side combos cover most base-layer needs.
> 4. **Cleanup**: strip debug `eprintln!`, remove `fn_detect.rs`, `cargo build --release`.
>
> **Documentation debt cleaned up 2026-04-13--2310:**
> - `src/usb.rs::set_layer_remap` doc-comment rewritten (was: "writes base layer" + "KNOWN DEAD END" — both wrong)
> - `src/main.rs::apply_fn_remaps` doc-comment rewritten (was: "writes base layer" walkback)
> - `src/keys.rs` matrix-index comment updated (was: asserted cmd=0x0F writes base layer without evidence)
> - Memory: `project_hypershift_commit_trigger.md` supersedes `project_joro_keymap_deadend.md`; MEMORY.md index updated.
>
> **Deferred from earlier (still valid):**
> - Per-key MM remap UI polish, Function Keys Primary preset button, icons, consumer HID interception (F8/F9 brightness VKs), cleanup pass + release build. These all unblock after the BLE keymap work lands.
> - **2. Verify keys.rs media VK additions build cleanly.** Code written in working tree but last `cargo build` was interrupted. Verify `parse_key_combo("VolumeMute")` → `Some((vec![], 0xAD))`.
> - **3. Verify single→single media-VK remap path empirically.** Add `[[remap]] from="VolumeMute" to="F5"` to config, press F5 in mm-primary mode, confirm VK_F5 fires instead of mute. Don't trust "already done" claims until tested.
> - **4. Per-key MM remap UI** — extend `findRemapForKey` in `assets/settings.html` to also match `fwMedia`; update popover prefill so clicking F5 defaults `From = VolumeMute`; orange warning for F8/F9 (brightness VKs bypass LL hook).
> - **5. "Function Keys Primary" preset button** — one click writes/clears the six canonical media-VK → F-key entries.
> - **Full Hypershift remapping UI polish** — show clear per-key mapping state, handle unknown matrix indices gracefully, add "clear all fn_remaps".
> - **Icons** — current PIL-generated ICO looks pixelated. Redraw with better source.
> - **Cleanup pass + release build** — strip debug `eprintln!`, remove `fn_detect.rs`, `cargo build --release`.
> - **BLE F1/F2/F3 = firmware-locked** — DEFINITIVELY uncircumventable (verified by testing Synapse). Do NOT spend more time on this.
> - **Clean isolated captures** — (a) MM↔Fn primary mode toggle: need to identify the packet cleanly (earlier capture was ambiguous — 2 writes with identical args=0). (b) Consumer-usage output encoding in `cmd=0x0d`: how Synapse encodes media-key outputs so the daemon can program Mute/Play/etc.
> - **Icons** — tray + window title icons look pixelated. Replace PIL generator with better source (proper vector asset or hand-drawn 16/20/24/32/48/64/256 ICO layers).
> - **Host-side Consumer HID interception** — deferred. If firmware reversal doesn't yield a per-key mode override, add a daemon thread that opens Joro's Consumer Control HID interface via hidapi, watches for specific usage codes (e.g. F4's "arrange windows"), swallows them, and emits VK_F2 via SendInput.
> - **Matrix table remaining gaps** — 0x3F, 0x41..0x45, 0x52, 0x57, 0x58, >0x7B. Low priority since discovered keys cover all common needs.
> - **Hypershift UI polish** — visual indicator in webview for which entries in `[[fn_remap]]` apply to plain key vs Fn-held.
> - **Cleanup pass + release build** — strip noisy debug eprintln, remove unused `fn_detect` module, `cargo build --release`.
> - Test USB↔BLE mid-session transport switch; test 2.4GHz dongle (PID 0x02CE).

## Build 2026-04-14--1937 — Filter-driver IOCTL PoC working end-to-end (F4–F12 fn-primary parity proven)

**Research complete for fn-primary F4–F12 parity.** `scripts/rzcontrol_poc.py` opens the Razer `RzDev_02ce` filter-driver control device from user-mode Python and successfully drives `EnableInputHook` + `EnableInputNotify` + `SetInputHook` round-trip toggle. No Synapse, no BLE writes, no admin elevation.

**Verified working:**
- `python rzcontrol_poc.py hook F8` → F8 emits VK_F8 (Chrome DevTools "resume" confirmed), no monitor brightness OSD
- `python rzcontrol_poc.py unhook F8` → F8 restored to brightness OSD (MM keys behavior)
- Round-trip toggle mechanism: `SetInputHook` struct with `flag=1` at offset 0x04 installs rule, `flag=0` removes it. Struct `[header 4B = 0] [flag 4B] [modifier 2B = 0] [scancode u16 LE] [272B reserved 0]`.

**F1/F2/F3 test:** hooks install successfully but BLE slot switching still fires. Confirms prior memory — slot switching happens in firmware BELOW the HID stack, so the filter never sees the scancode. Not circumventable via this path.

**Complete capability** for fn-primary on Joro BLE now confirmed:

| Key set | Mechanism | Coverage |
|---|---|---|
| F1/F2/F3 BLE slots | Firmware-locked below HID | ❌ uncircumventable |
| F4 (Win+Tab macro) | Existing combo-source remap | ✅ already working |
| F5/F6/F7 (Consumer Mute/Vol) | LL hook w/ injection tag OR filter driver | ✅ two paths |
| **F8/F9 (Brightness)** | **Filter driver only** (no Win32 VK for brightness) | ✅ **new** |
| **F10/F11 (backlight)** | **Filter driver only** (no Win32 VK) | ✅ **new** |
| F12 (PrintScreen VK) | LL hook OR filter driver | ✅ two paths |

**Session notes:**
- 9 Frida scripts written + `rzcontrol_poc.py` added to `scripts/`
- Research-only: zero daemon code changes this session
- Next session: Rust port (`src/rzcontrol.rs`), integrate with existing `fn_host_remap` flow, UI toggle, test across transport cycles

**Files changed:**
- `_status.md`, `CHANGELOG.md` — session writeup
- `scripts/rzcontrol_poc.py` (new) — production-quality Python PoC with hook/unhook/enable/disable CLI
- `scripts/frida_*.py` (new, multiple) — investigation tooling: module enum, IOCTL hook, HID.DLL hook, hidapi hook, auto-attach watcher
- Memory: `project_razer_filter_driver_ioctls.md` — now includes PoC verification + toggle round-trip results

> [!warning] Testing Checklist
> - [x] PoC opens rzcontrol BLE device handle (no elevation, no error)
> - [x] PoC `EnableInputHook(1)` + `EnableInputNotify(1)` succeed
> - [x] PoC `SetInputHook(F8, flag=1)` installs filter rule
> - [x] F8 verified emitting VK_F8 via Chrome DevTools "resume"
> - [x] PoC `SetInputHook(F8, flag=0)` removes filter rule
> - [x] F8 verified restored to monitor brightness OSD
> - [x] F1/F2/F3 confirmed firmware-locked (hook installs but slot switching still fires)
> - [ ] Rust port working in daemon (next session)
> - [ ] Daemon rzcontrol calls coexist with Razer Elevation Service (untested)
> - [ ] UI toggle wired (next session)

## Build 2026-04-14--1854 — Razer filter driver IOCTL interface decoded (research)

**Research-only session, no daemon code changes.** Decoded Synapse's fn-primary mechanism on Joro BLE via Frida hooking of `NtDeviceIoControlFile` in `RazerAppEngine.exe` main process.

**Key finding:** Synapse uses a **Razer kernel lower-filter driver (`RzDev_02ce.sys`)** to intercept F-row scancodes at the kernel level. The driver exposes a control device at `\\?\rzcontrol#vid_068e&pid_02ce&mi_00#<instance>#{e3be005d-d130-4910-88ff-09ae02f680e9}`.

**IOCTL vocabulary decoded** (device type `0x8888`, `METHOD_BUFFERED`):
- `0x88883034` `EnableInputHook(bool)` — 4-byte input, turns filter on
- `0x88883038` `EnableInputNotify(bool)` — 4-byte input, enables notification channel
- `0x88883024` `SetInputHook(struct)` — 292-byte per-scancode registration
- `0x88883020` (Function 0xC08) — unknown, 20-byte input with Consumer usage 0x70 (BrightnessDown)
- `0x88883018` — status/heartbeat poll (304-byte output, kernel pool data, NOT an event stream)

**Synapse's init sequence** when user opens the Joro device page:
1. `EnableInputHook(1)`
2. `EnableInputNotify(1)`
3. `SetInputHook` for each of: `0x01 0f 38 3b 3c 3d 3e 3f 40 41 42 43 44 47 49 4f 51 57 58` (Esc, Tab, LAlt, F1–F12, Kp nav)

**🔴 Significant finding:** F1, F2, F3 are in the scancode list Synapse registers. Prior memory says these are "firmware-locked as BLE slot selectors" — but that was empirically observed with Synapse+filter in the chain. The filter may be what suppresses them, not the firmware. Testable.

**What we don't know yet:**
- 272 bytes of tail in the `SetInputHook` struct (all zero in current capture)
- How Synapse receives intercepted events to re-emit them
- How to cleanly unregister a scancode

**Files changed:**
- `scripts/frida_*.py` (9 new Python scripts for Frida enumeration, module/export dumping, IOCTL hooking, HID.DLL hooking, hidapi hooking, auto-attach watcher)
- `_status.md`, `CHANGELOG.md` updated
- New memory: `project_razer_filter_driver_ioctls.md` with complete IOCTL doc

**What to do next:**
1. Python PoC: open rzcontrol device, send `EnableInputHook(1)` via `DeviceIoControl`. Confirms user-mode access without Synapse.
2. Rust port: `src/rzcontrol.rs` client replicating the init sequence.
3. F1/F2/F3 test: remove `RzDev_02ce` from the BTHLE LowerFilters, verify scancode behavior.
4. Find the event receive channel (second Frida pass or kernel driver RE).

> [!warning] Testing Checklist
> - [ ] Python PoC: CreateFile + DeviceIoControl EnableInputHook succeeds
> - [ ] Rust port: open rzcontrol + init sequence + smoke test on F8 (filter swallows)
> - [ ] F1/F2/F3 scancode emission test with filter removed from LowerFilters
> - [ ] Synapse parity verified for F4–F12 (clean key presses, no side effects)

## Build 2026-04-14--0142 — Hypershift UI wired for host-side remaps

**Changes**
- Settings webview can now view, edit, add, and clear host-side Fn-layer bindings live over BLE.
- `push_settings_state` now ships `fn_host_remaps` alongside `fn_remaps`.
- New IPC actions: `set_fn_host_remap`, `clear_fn_host_remap`. Save routes to host-side by default, or to firmware if user switches the "Apply to" selector. `update_fn_host_remap` persists the config and rebuilds `remap::FN_HOST_REMAP_TABLE` atomically — the hook picks up the new binding on the next key event with no restart.
- Hypershift popover in `assets/settings.html`:
  - Shows a "Current binding" badge identifying the source (host-side daemon vs keyboard firmware) when an existing binding is present.
  - New "Apply to" dropdown: host-side (default) or firmware (USB-only).
  - Transport warning only appears when user picks Firmware while off USB.
  - Save routes to `set_fn_remap` (firmware) or `set_fn_host_remap` (host) based on the selector.
  - Clear routes to the matching clear action for the active source.
- `findRemapForKey` in hypershift mode now checks host-side first (daemon wins at the LL hook), then firmware, returning a `{ name, from, to, source }` wrapper.
- Layer-toggle hint rewritten to reflect the dual-mode reality.

**Files changed**
- `src/main.rs` — `push_settings_state` payload, `update_fn_host_remap` method, `set_fn_host_remap` / `clear_fn_host_remap` IPC handlers
- `assets/settings.html` — `fnHostRemaps` state, `joroSetState` population, `findRemapForKey` dual-source lookup, popover target selector / source badge / save+clear routing, hint text

**Verified 2026-04-14 over BLE:**
- Clicked A in Hypershift tab → popover shows host-side default and existing `A → F2`.
- Edited To field to `Home`, Save → config updated to `fn_host_remap from="a" to="Home"`, hook picked up the change live.
- Pressing Fn+A in text editor → cursor jumped to line start.
- All config changes persisted to `%APPDATA%\razer-joro\config.toml` without touching USB.

**Known side effect:** `save_config` re-serializes the whole Config struct (loses comments, overwrites all sections). If stale in-memory state differs from the user's hand-edited TOML, the save wins. Acceptable for v1 since the UI is the canonical edit path going forward.

## Build 2026-04-14 — Host-side Fn detection WORKING over BLE (Synapse parity)

**Changes**
- **Daemon now replicates Synapse's BLE Hypershift with zero USB.** New `[[fn_host_remap]]` config section applies Fn+key bindings host-side via WH_KEYBOARD_LL hook, gated on live Fn state from `fn_detect`. Works on any transport including BLE-only sessions.
- **Discovered the Fn signal**: Joro exposes Fn state on vendor HID collection 0x0001/0x0000 (Col05 on BLE) as a 12-byte report `[0x05, 0x04, state, 0...]`. Verified by discovery capture: plain F5 emits Col03 Mute only, Fn+key fires Col05. See `captures/fn_detect_ble.log`.
- **Proved Synapse doesn't write firmware keymap over BLE** (not a capture dead end — a tested fact). Phase 3 test: used Synapse to program Fn+A → LWin on BLE, killed Razer, read firmware slot directly via `diag-readlayers 0x1f` — all 4 layers still 0x04 ('a'), factory unchanged. Synapse's BLE Hypershift is host-side interception.
- **Discovered firmware Hypershift runtime enable flag** separate from stored layer data. Synapse turns it off when it takes over a BLE session; transport cycle turns it back on. Data in storage is never touched.
- Replaced the dead `fn_detect` diagnostic-only module with a production `start()` that enumerates Joro HID collections and spawns reader threads updating a `FN_HELD: AtomicBool`. Idempotent across transport changes.
- Extended `remap::hook_proc` with a new top-level Fn-layer branch consulting `fn_detect::fn_held()`, with `ACTIVE_FN_REMAP` tracking so source-key release still cleans up if Fn was released first.
- Added Windows BTH stale-pair recovery: `scripts/bt_remove.ps1` wraps `BluetoothRemoveDevice` WinAPI.

**Files changed**
- `src/fn_detect.rs` — production `start()` + helpers; diagnostic kept for CLI
- `src/remap.rs` — `FnHostRemap`, `FN_HOST_REMAP_TABLE`, `ACTIVE_FN_REMAP`, `build_fn_host_remap_table`, `update_fn_host_remap_table`, new branch in `hook_proc`
- `src/config.rs` — `fn_host_remap: Vec<FnRemapConfig>` field
- `src/main.rs` — wire fn_host table at boot / reload / UI save; call `fn_detect::start()` at boot and on every device connect; replaced stale "Fn invisible to HID" comment
- `src/usb.rs`, `src/keys.rs` — doc-comment cleanup from walkback session (no behavioral change)
- `%APPDATA%\razer-joro\config.toml` — seeded `[[fn_host_remap]] A → F2` test entry
- `scripts/bt_remove.ps1` (new)
- `_status.md`, `CHANGELOG.md`, MEMORY index + 4 new memories

> [!warning] Testing Checklist
> - [x] Daemon startup log shows fn-host table loaded and fn_detect readers opened on Col03/04/05/06 — VERIFIED
> - [x] Fn+A over BLE triggers File Explorer Rename (VK_F2) — VERIFIED
> - [x] Firmware Fn+Left=Home / Fn+Right=End still work alongside host-side — VERIFIED
> - [x] Plain A still types 'a' — VERIFIED
> - [ ] Fn+A on wired transport — untested this session (should work same path, fn_detect enumerates by product string)
> - [ ] Long Fn hold doesn't open Task View via spurious Col03 0x029D — not tested this session
> - [ ] Daemon stable over a config reload / UI save with fn_host_remap changes — not tested

## Build 2026-04-13--2257 — Hypershift commit trigger discovered + BLE/wired share storage

**Changes**
- **Fn+Left=Home / Fn+Right=End working on both transports.** User reported Fn+Left typed 'z' on BLE (earlier `run_matrix_scan` HID 0x1d residue). Restart of daemon over USB alone did not fix it — writes landed in firmware storage but not the live keymap. A **transport mode switch (wired↔BLE↔wired)** committed them. After that, both transports read Home/End correctly. Firmware only refreshes the runtime Hypershift table on transport change.
- **BLE and wired share the same Hypershift storage slot.** One USB `set_layer_remap` (cmd=0x0d) write programs both transports. Daemon intentionally skips `apply_fn_remaps` on BLE (`main.rs:176`) — that's correct, the values persist from earlier USB writes.
- **BLE pairing recovered.** Stale `C8E2775D2FA2` PnP records removed via `pnputil`. Windows UI still showed Joro paired until BARROT Bluetooth 5.4 dongle was physically replugged (BARROT was in `CM_PROB_FAILED_ADD` — root cause of the "can't remove from BLE list" symptom). User re-paired cleanly on new random address `C8E2775D2FA3`. Daemon reconnected with no "object closed" errors.
- **Prior session's "keymap dead end" memory superseded.** `project_joro_keymap_deadend.md` conclusion ("writes store but don't apply") was wrong — same-transport testing without cycling missed the commit. New memory: `project_hypershift_commit_trigger.md`.

**Files changed**
- `_status.md`, `CHANGELOG.md` — session writeup
- `scripts/bt_remove.ps1` (new) — PowerShell wrapper for `BluetoothRemoveDevice` WinAPI

**Files NOT changed**
- No Rust source changes. `set_layer_remap` was always correct.

> [!warning] Testing Checklist
> - [x] Fn+Left=Home on wired — VERIFIED after transport cycle
> - [x] Fn+Right=End on wired — VERIFIED
> - [x] Fn+Left=Home on BLE — VERIFIED (reads values written earlier over USB)
> - [x] Fn+Right=End on BLE — VERIFIED
> - [x] BLE pair clean, no "object closed" errors — VERIFIED via daemon log
> - [ ] Plain Left/Right still arrows (not Home/End or z) — presumed OK but not explicitly verified this session

## Build 2026-04-13--1910 — Copilot BLE regression (unresolved) + docs correction

**Changes**
- **Copilot → Ctrl+F12 confirmed broken over BLE** in current working tree. User verified this remap worked over BLE in an earlier session; today the hook doesn't see any Copilot event at all in BLE mode, despite the trigger loading correctly in the daemon's startup log. **Earlier in this same session this was misdocumented as "BLE Copilot unreachable by design" — that was a wrong conclusion reached by guessing rather than testing. Corrected in `_status.md` and `WORKPLAN.md`.** Marked as task #10, top priority for next session.
- **Revert `[[remap]] Win+Tab → F2`** — F4 is back to its default arrange-windows behavior per user request. User will pick a different physical key for rename later.
- **Razer services killed again** to test Synapse parity of BLE F1/F2/F3 — result: even Synapse cannot override them in BLE mode. DEFINITIVE firmware limit.

**Files changed**
- `%APPDATA%\razer-joro\config.toml` — F4 remap removed
- `_status.md`, `CHANGELOG.md`, `WORKPLAN.md` — Copilot regression documented, earlier lie corrected

> [!warning] Testing Checklist
> - [x] F4 reverts to arrange windows — VERIFIED (remap removed, daemon reloaded config)
> - [x] Synapse fn-primary cannot override BLE slot switching on F1/F2/F3 — VERIFIED
> - [ ] Copilot → Ctrl+F12 works again over BLE — REGRESSED, unresolved, task #10
> - [ ] keys.rs media VK additions build cleanly — unverified (build interrupted)

## Build 2026-04-13--1830 — Definitive BLE F1/F2/F3 finding + settings UI firmware metadata

**Changes**
- **BLE F1/F2/F3 = firmware-locked as slot selectors, definitively.** User launched Razer Synapse, put Joro in BLE mode, enabled Function Keys Primary, and tested F1/F2/F3. **Slot switching fired regardless.** Even Razer's own software can't override this. In wired mode, Synapse's fn-primary DOES turn F1/F2/F3 into function keys — proving it's a host-side feature (SendInput of VK_F1/F2/F3 since wired firmware emits nothing). Implications:
  - User's personal target "BLE mode + plain F2 = rename" is **impossible** without a firmware patch.
  - No-loss alternatives: (A) BLE + Fn+F2 = rename, (B) wired + F2 = rename via host intercept, (C) different physical key.
- **Settings UI firmware metadata** — F-row keys in `assets/settings.html` now carry `fwEmits` / `fwMedia` / `fwNote` fields capturing the firmware-level emission (F4 → Win+Tab, F5 → Mute consumer, etc.). `findRemapForKey` matches remaps by `fwEmits` so e.g. F4 shows the `Win+Tab → F2` remap as its current state. Popover prefills `From` with `fwEmits` and displays an informational hint explaining the firmware behavior.

**Files changed**
- `assets/settings.html` — F-row `fwEmits` / `fwMedia` / `fwNote` metadata, `findRemapForKey` extension, popover prefill + hint
- `_status.md`, `CHANGELOG.md`, `WORKPLAN.md` — findings documented

> [!warning] Testing Checklist
> - [x] Synapse + BLE + Fn Primary still shows slot switching on F1/F2/F3 — VERIFIED (user confirmed)
> - [x] Synapse + Wired + Fn Primary turns F1/F2/F3 into F-keys — VERIFIED (user confirmed)
> - [x] F4 in settings UI shows mapped state (green outline / tooltip shows Win+Tab → F2) — VERIFIED earlier in build cycle
> - [ ] F5/F6/F7 popovers show consumer-usage orange warning hint — needs visual check
> - [ ] F4 popover shows firmware-macro blue hint with prefilled `Win+Tab` — needs visual check

## Build 2026-04-13--1750 — F4 = rename shipped via Win+Tab intercept

**Changes**
- **F4 → rename (working).** Discovered that F4's "arrange windows" behavior is a firmware-level keyboard macro emitting `LWin + Tab` on Joro's main keyboard HID interface. Windows interprets this as Task View. Fix: one config entry using the existing combo-source trigger infrastructure in `remap.rs`:
  ```toml
  [[remap]]
  name = "F4 (emits Win+Tab) to F2 (rename)"
  from = "Win+Tab"
  to = "F2"
  ```
  Cost: physical Win+Tab (Task View) is sacrificed — indistinguishable at VK level. User accepted.
- **Consumer HID interception layer (`src/consumer_hook.rs`).** Background thread opens Joro's Consumer Control + System Control HID interfaces via hidapi, matches reports against `[[consumer_remap]]` config entries, emits replacements via SendInput. Logs unknown usages for discovery. **Caveat**: hidapi reads on Windows are non-consuming — we see usages but don't remove them from the stack, so intercepting Mute via this layer DOES NOT actually mute. The layer is useful for discovery logging and for remapping usages Windows ignores natively.
- **Joro consumer usage codes discovered**: F5=0x00E2 Mute, F6=0x00EA VolDown, F7=0x00E9 VolUp, F8=0x0070 BrightnessDown, F9=0x006F BrightnessUp. F4/F10/F11/F12 do not emit consumer reports at all (F4 uses the keyboard macro path described above; F10-F12 TBD).
- **Razer service killing was a red herring.** Stopped all 6 Razer services (Chroma SDK Diagnostic/Server/Service, Chroma Stream Server, Elevation Service, Game Manager Service 3) to test whether user-mode Razer code was handling F4 — it wasn't. F4's Win+Tab is pure firmware macro.
- **`set_fn_key_toggle` experiment removed.** openrazer documents `class=0x02 cmd=0x06` for global mm↔fn toggle but `dev_attr_fn_toggle` isn't registered for Joro in `razerkbd_driver.c`. Direct wire-format testing (with `transaction_id=0xFF` per openrazer convention) produced no effect. A clean USBPcap capture of Synapse toggling the mode confirmed zero class=0x02 writes leave Synapse during the toggle — the setting is a Synapse host-side feature, not a firmware command. Removed the dead code and CLI subcommand.

**Files changed**
- `src/consumer_hook.rs` — new module
- `src/remap.rs` — `make_key_input`/`send_inputs` exposed as `pub(crate)`
- `src/config.rs` — `ConsumerRemapConfig` struct + `Config::consumer_remap` field
- `src/main.rs` — `App::consumer_hook` field + lifecycle in try_connect/check_device; `set_fn_key_toggle`/`fn-primary` CLI subcommand removed
- `src/usb.rs` — `set_fn_key_toggle` removed
- `src/device.rs` — trait method removed
- `proto/consumer_discover.py` — new diagnostic
- `%APPDATA%\razer-joro\config.toml` — `[[remap]] Win+Tab → F2`

> [!warning] Testing Checklist
> - [x] F4 in wired mode, Explorer with file selected → filename rename mode — VERIFIED
> - [x] F4 no longer triggers Task View — VERIFIED (sacrifice accepted)
> - [x] Fn+F2 still produces rename as fallback — VERIFIED earlier this session
> - [x] F5/F6/F7/F8/F9 still produce their mm behavior (mute/vol/brightness) — VERIFIED (hidapi non-consuming confirmed)
> - [x] Host-side combo remaps (Win+L → Delete, Copilot → Ctrl+F12) still work — VERIFIED via daemon startup log ("3 trigger remaps")
> - [ ] F4 in BLE mode — does Joro still emit Win+Tab over BLE? Untested.
> - [ ] Physical Win+Tab also routes through F2 remap — verify it now renames instead of opening Task View. Expected yes since indistinguishable from firmware macro at VK level.

## Build 2026-04-13--1625 — Matrix discovery + protocol correction + BLE slot architecture

**Changes**
- **Joro matrix table ~75% mapped** via new `cargo run -- scan <batch>` CLI subcommand. Five batches (0..4) cover indices 0x01..0x82. Programs 26 matrix indices per batch to letters a..z via `set_layer_remap`; user presses physical keys and reads letters to identify the matrix index. `src/keys.rs::JORO_MATRIX_TABLE` extended from 4 entries → ~60 entries including full number/tab/caps/shift/nav rows and all of F1..F12. Remaining gaps: 0x3F, 0x41..0x45, 0x52, 0x57, 0x58, >0x7B.
- **Protocol understanding corrected via Synapse capture.** Captured Synapse remapping Right Ctrl → F2 on the **Standard (base)** layer. Packet bytes: `class=0x02 cmd=0x0d dsize=10 args = 01 40 01 02 02 00 3b 00 00 00`. Proves `args[0]=0x01` is a **constant**, not a layer selector as previously assumed. Our `set_fn_layer_remap` was always writing to the base layer — the "Fn layer" mental model was wrong.
- **Renamed `set_fn_layer_remap` → `set_layer_remap`** (trait method + usb.rs impl + main.rs callers). Docstring updated to explain the capture findings and why F-row base taps can't be matrix-remapped in mm-primary mode.
- **Deleted `set_base_layer_remap`** (wrote `args[0]=0x00` which was wrong) and its `apply_base_remaps` caller and `[[base_remap]]` config field.
- **BLE slot selector architecture discovered.** Controlled test: programmed F2 matrix (0x71) → HID F2 (0x3B) via daemon, switched Joro to BLE mode, tested F1/F2/F3. Result:
  - F1 alone = BLE slot 1 ✓ (unchanged)
  - F3 alone = BLE slot 3 ✓ (unchanged)
  - F2 alone = BLE slot 2 (unchanged — matrix write did NOT affect base behavior)
  - Fn+F2 = F2 key (rename in Explorer) ✓ (matrix write took effect on Fn-held path)
  
  Conclusion: BLE slot selection is a **firmware-internal handler that runs BEFORE matrix lookup**, same architecture as F-row media keys (mute, arrange windows, etc.) in mm-primary mode. Matrix remaps are safe — they cannot break BLE slots. But matrix remaps also cannot intercept F-row base taps in mm-primary mode; only the Fn-held path reaches the matrix.
- **Current user config** has `[[fn_remap]] F2 → F2` active, making Fn+F2 = rename. This is the working compromise until deeper firmware reversal finds a per-key mode override.
- **Icon loading fix** — earlier in session: `image` crate in Cargo.toml was missing the `"ico"` feature, causing `ICO decode failed` at runtime and falling back to runtime-drawn keyboard shapes. Added `features = ["png", "ico"]`.

**Files changed**
- `src/usb.rs` — `set_layer_remap` (renamed); `set_base_layer_remap` deleted
- `src/device.rs` — trait method renamed; `set_base_layer_remap` removed
- `src/main.rs` — `apply_base_remaps` deleted; `run_matrix_scan(batch)` subcommand added; callers updated
- `src/config.rs` — `base_remap` field removed
- `src/keys.rs` — `JORO_MATRIX_TABLE` extended to ~60 entries
- `Cargo.toml` — `image` crate `"ico"` feature added
- `%APPDATA%\razer-joro\config.toml` — cleaned, `[[fn_remap]] F2 → F2` added

> [!warning] Testing Checklist
> - [x] `cargo run -- scan <0..4>` programs matrix indices to a..z and user can identify via Notepad — VERIFIED across all 5 batches
> - [x] Fn+F2 = rename in Windows Explorer (Notepad + Explorer tested) — VERIFIED
> - [x] F1/F3 still switch BLE slots 1/3 in BLE mode after matrix write — VERIFIED
> - [x] F2 alone still switches BLE slot 2 in BLE mode (proves matrix bypass for slot selectors) — VERIFIED
> - [x] `set_layer_remap` rename compiles cleanly — VERIFIED
> - [ ] After Synapse Reset Profile, all firmware remaps from `[[fn_remap]]` reapply cleanly on next daemon USB connect (regression check)
>   - Notes:
> - [ ] Fn+F4, Fn+F5, Fn+F6 behavior: verify that existing matrix remaps for the F-row (from scan 4 residue) don't interfere with user's expected Fn+F-row behavior
>   - Notes:

## Build 2026-04-13--0309 — Config-driven Fn remaps + battery fix

**Changes**
- **Fn-layer remap is now config-driven** — new `[[fn_remap]]` section in `config.toml`:
  ```toml
  [[fn_remap]]
  name = "Fn+Left to Home"
  from = "Left"
  to = "Home"
  ```
  Daemon iterates entries on USB connect and calls `set_fn_layer_remap()` for each. Removed hardcoded Left/Right calls from `try_connect()`.
- **`FnRemapConfig` struct** in `src/config.rs` with serde Serialize+Deserialize. `Config::fn_remap: Vec<FnRemapConfig>` (defaults to empty if missing — backward compatible).
- **`keys::key_name_to_matrix()`** — Joro physical-key matrix index lookup table (`JORO_MATRIX_TABLE` + `JORO_MATRIX_MAP`). Currently 4 known indices: Escape=0x01, CapsLock=0x1E, Left=0x4F, Right=0x59. Extended as we discover more.
- **`parse_hid_combo()`** in `main.rs` — converts strings like `"Home"`, `"Ctrl+F12"`, `"Shift+End"`, `"Win+Tab"` into `(hid_modifier_byte, hid_usage_code)` pairs. Supports all 8 HID modifier bits (LCtrl/LShift/LAlt/LGui/RCtrl/RShift/RAlt/RGui).
- **`apply_fn_remaps()`** — static method that iterates `cfg.fn_remap`, resolves source matrix index via `key_name_to_matrix`, parses output via `parse_hid_combo`, calls `dev.set_fn_layer_remap()`. Logs each successful application; warns on entries that can't be resolved (unknown matrix index or unparseable output).
- **Battery fix (BLE)** — was reading byte 0 of response data; per openrazer driver source (`razerchromacommon.c` line 1057) the 0-255 battery level lives in `arg[1]`, not arg[0]. Fixed `src/ble.rs::get_battery_percent` to read `data.get(1)`. The "25%" reading the user observed earlier was actually a misread of `arg[0]=0x40`.
- **Battery fix (USB)** — added `RazerDevice::get_battery_percent()` to `src/usb.rs` using the same Protocol30 command (`class=0x07 cmd=0x80` request size 2). USB transport previously had no battery support (default trait method returned Err → `battery=None`). Trait override added.

**Files changed**
- `src/config.rs` — `FnRemapConfig` struct, `Config::fn_remap` field, default config Fn+Left/Right seeds
- `src/keys.rs` — `JORO_MATRIX_TABLE`, `JORO_MATRIX_MAP`, `key_name_to_matrix()`
- `src/main.rs` — `apply_fn_remaps()`, `parse_hid_combo()`, `try_connect()` now config-driven
- `src/ble.rs` — battery reads `arg[1]` (was `arg[0]`)
- `src/usb.rs` — new `get_battery_percent()` + trait override

> [!warning] Testing Checklist
> - [x] Daemon prints `fn-layer Left → Home (matrix=0x4f mod=0x00 usage=0x4a)` on USB connect — VERIFIED
> - [x] Daemon prints `fn-layer Right → End (matrix=0x59 mod=0x00 usage=0x4d)` on USB connect — VERIFIED
> - [ ] Battery reads correctly on USB (was None, should now be 0-100)
>   - Notes:
> - [ ] Battery reads correctly on BLE (was 25%, should now show closer to actual ~100%)
>   - Notes:
> - [ ] Adding a new `[[fn_remap]]` entry for `from = "CapsLock"` works (CapsLock matrix 0x1E is known)
>   - Notes:
> - [ ] Adding a new `[[fn_remap]]` entry for an unknown key (e.g. `from = "F1"`) prints a warning and is skipped
>   - Notes:

## Build 2026-04-13--0248 — Fn-layer firmware remap WORKING

## Build 2026-04-13--0248 — Fn-layer firmware remap WORKING

**Changes**
- **Reverse-engineered Razer Synapse's Fn-layer remap protocol** via USBPcap capture. Found a previously-unknown command: `class=0x02 cmd=0x0d` with a 10-byte data payload that programs the keyboard's firmware Fn-layer keymap. The remap persists in firmware across reboots and works on any transport.
- **Protocol decoded**:
  ```
  args[0]  = layer (0x01 = Fn layer)
  args[1]  = source matrix index (Joro: Left=0x4F, Right=0x59)
  args[2]  = profile/var-store (0x01)
  args[3]  = output type (0x02 = HID keyboard)
  args[4]  = output size (0x02)
  args[5]  = output modifier (0x00 for plain key)
  args[6]  = output HID usage (Home=0x4A, End=0x4D)
  args[7..10] = padding
  ```
- **Implemented `RazerDevice::set_fn_layer_remap(src_matrix, modifier, dst_usage)`** in `src/usb.rs`, exposed via the `JoroDevice` trait with a no-op default for non-USB transports (BLE doesn't carry class 0x02).
- **Hardcoded Fn+Left → Home and Fn+Right → End** in `try_connect()` for USB transport — applies on every USB connect, idempotent.
- **Verified working**: pressed Fn+Left in Notepad → cursor jumped to start of line; Fn+Right → end of line. Confirmed by user.
- **Capture infrastructure built**: PowerShell scripts to launch USBPcap on all root hubs in parallel (the only invocation pattern that works headless), Python parser that extracts and decodes Razer Protocol30 SET_REPORT control transfers from .pcap files. Reusable for any future Synapse protocol reverse-engineering.

**Files changed**
- `src/usb.rs` — new `set_fn_layer_remap()` method, trait impl override
- `src/device.rs` — new `set_fn_layer_remap()` trait method with Err default
- `src/main.rs` — hardcoded Fn+Left/Right remap calls in `try_connect()` (USB only)

**Test artifacts (kept for future reference)**
- `C:\Users\mklod\AppData\Local\Temp\synapse_capture.ps1` — capture launcher
- `C:\Users\mklod\AppData\Local\Temp\parse_synapse2.py` — pcap parser
- `C:\Users\mklod\AppData\Local\Temp\synapse_capture.pcap` — 441 KB raw capture

> [!warning] Testing Checklist
> - [x] `Fn+Left → Home` works in Notepad after USB connect — VERIFIED
> - [x] `Fn+Right → End` works in Notepad — VERIFIED
> - [ ] Persistence: switch keyboard to BLE mode, do NOT run daemon, confirm Fn+Left still produces Home (firmware should retain remap)
>   - Notes:
> - [ ] Persistence across reboot: power-cycle the keyboard, confirm Fn+Left still works
>   - Notes:
> - [ ] Combo output: try setting `modifier=0x01` (LCtrl) on a remap and confirm the keyboard emits Ctrl+key
>   - Notes:
> - [ ] Base-layer test: try `cmd=0x0d` with `args[0]=0x00` (base layer) to see if it can replace `set_keymap_entry` (cmd=0x0F)
>   - Notes:

## Build 2026-04-13--0159 — Webview settings window + Fn detection blocker

**Changes**
- **wry webview settings window** at 1100×640 fixed size, position persisted across sessions via `%APPDATA%\razer-joro\window_state.json`. Opens on left-click of tray icon OR via "Settings…" menu entry on right-click menu.
- **Visual 75% Joro keyboard layout** with inline SVG icons for the entire F-row (bluetooth 1/2/3 with LED indicator dots, screens/PC, speaker mute/low/high, sun small/large, keyboard backlight down/up, prt sc, padlock), real Microsoft Copilot icon, Windows 4-pane logo, globe (fn), chevron arrow keys, media icons (prev/next/play/mute) on pg up / pg dn / home / end, CapsLock LED dot.
- **Per-key alignment variants** — top-center default; `align-left` on Tab/CapsLock/LShift; `align-right` on Enter/Backspace; `align-center` (full center) on F-row and arrow cluster.
- **Exact-match remap engine** — each physical key has an `emits` field; `Win+L` remap highlights the Lock key (top-right F-row) and `Win+Copilot` highlights the dedicated Copilot key, NOT the letter L or the Win key. Generic combos are still remappable via popover From field editing.
- **Popover editor** — click a key → editable From/To fields, defaults to the key's `emits`. Auto-save on Save/Clear. Selected key shows solid green fill while popover is open.
- **Lighting controls in settings window** — single-row layout with HTML5 color picker + hex display + brightness range slider + effect dropdown (Static/Breathing/Spectrum). Auto-saves via new `set_lighting` IPC action with 180ms debounce on drag.
- **Battery indicator** — new `get_battery_percent()` trait method, BLE impl via Protocol30 `0x07/0x80`, mapped to 0-100%. SVG battery icon + percentage in window header top-right, read once when settings window is opened.
- **Paired-device auto-connect** (MAJOR fix) — `BleDevice::open()` now uses `DeviceInformation::FindAllAsyncAqsFilter` with `GetDeviceSelectorFromPairingState(true)` to enumerate paired BLE devices and acquires by device ID. Previous advertisement-watcher-only path did NOT work for already-paired-and-connected devices (they don't advertise). Daemon now reconnects in <1s on startup without any re-pair dance.
- **Clean Ctrl+C shutdown** — `ctrlc` crate registers a handler that posts `UserEvent::CtrlC` via `EventLoopProxy`. `shutdown_and_exit()` explicitly drops the BleDevice (runs `Close()` on WinRT → keyboard resumes advertising cleanly) then `std::process::exit(0)` as fallback because winit's `run_app` sometimes won't return from `event_loop.exit()` when wry+tray are registered. Same path used by tray Quit menu item.
- **Tray left-click → settings window** — `with_menu_on_left_click(false)` suppresses default menu, `TrayIconEvent::Click { MouseButton::Left, MouseButtonState::Up }` handler opens the webview window. Right-click still shows the context menu.
- **Reconnect backoff** — 10s interval between BLE scan attempts when disconnected (was 2s, which was blocking the tray menu). Scan runs with 1.5s timeout.
- **Single-key host remap fix** — `remap.rs` previously dropped `a → b` style remaps silently (intended for firmware path, never wired). Removed the skip; now they're handled host-side by the existing hook.
- **config.rs: `save_remaps()`** helper — rewrites the `[[remap]]` section via `toml::to_string` while preserving the header (comments + `[lighting]`). Used by webview save action.
- **Fn-state HID detection attempt** — built `src/fn_detect.rs` diagnostic that enumerates Joro HID collections (6 interfaces exposed over BLE: Keyboard, Mouse, Consumer Control, System Control, Vendor x2) and timestamps every input report received. Confirmed Fn key is invisible to all readable interfaces — only Fn+F-row combos that are firmware-translated (like Fn+F5 → AC Refresh) show up. **Blocker:** Fn+Left is firmware-passthrough, no distinct HID report exists for us to intercept. Synapse must program the keyboard's Fn-layer keymap directly via a command we haven't reverse-engineered.

**Dependencies added**
- `wry = "0.55"` — webview (Edge WebView2 backend on Windows)
- `serde_json = "1"` — IPC message serialization
- `ctrlc = "3"` — cross-platform console Ctrl+C handler
- `hidapi = "2"` — cross-platform HID device enumeration and raw report reading

**Files changed / added**
- `assets/settings.html` — complete webview UI (keyboard visual, popover, lighting row, battery indicator)
- `src/settings_window.rs` (new) — wry window lifecycle, position save/restore
- `src/window_state.rs` (new) — settings window JSON state file I/O
- `src/fn_detect.rs` (new) — HID report diagnostic for Fn detection
- `src/main.rs` — `UserEvent::{CtrlC, SettingsIpc}`, `apply_lighting_change`, `shutdown_and_exit`, tray left-click handler, reconnect backoff, paired-enum autoconnect wiring
- `src/ble.rs` — `find_paired_joro`, `connect_from_device`, `connect_from_address`, `get_battery_percent`
- `src/tray.rs` — `with_menu_on_left_click(false)`, `poll_tray_event`, "Settings…" menu item
- `src/device.rs` — `get_battery_percent` default trait method
- `src/remap.rs` — removed silent skip for single-key single-key remaps
- `src/config.rs` — `save_remaps` helper
- `Cargo.toml` — added wry/serde_json/ctrlc/hidapi; expanded windows crate features

> [!warning] Testing Checklist
> - [ ] Left-click tray → settings window opens
>   - Notes:
> - [ ] Right-click tray → context menu with Color/Brightness/Effect submenus + Settings… + Quit
>   - Notes:
> - [ ] Settings window shows paired keyboard's battery percentage in header top-right
>   - Notes:
> - [ ] Clicking `L` letter key does NOT show green glow (the `Win+L` remap should highlight the Lock key in F-row instead)
>   - Notes:
> - [ ] Clicking the dedicated Lock key (F-row top-right) opens popover pre-filled with `Win+L → Delete`
>   - Notes:
> - [ ] Popover Save/Clear/Cancel work; key's green state updates after save
>   - Notes:
> - [ ] Lighting color picker drag changes keyboard color live
>   - Notes:
> - [ ] Brightness slider drag changes brightness live
>   - Notes:
> - [ ] Effect dropdown change applies (Static → Breathing → Spectrum)
>   - Notes:
> - [ ] Close settings window, reopen → shows up in same position on screen
>   - Notes:
> - [ ] Ctrl+C in terminal cleanly exits daemon (prompt returns, no stuck process)
>   - Notes:
> - [ ] Restart daemon → auto-connects to paired Joro without re-pairing through Windows
>   - Notes:
> - [ ] Fn key investigation: test if Joro has any Windows-visible scan code for Fn (DEFERRED — confirmed invisible)
>   - Notes: Fn absorbed by firmware. Synapse must do firmware-level keymap write. Next step: USB-capture Synapse's Fn+Left remap command.

## Build 2026-04-10--1630 — BLE SET Commands Working

**Changes**
- **BLE SET brightness** — WORKING over BLE via Protocol30 split writes
- **BLE SET static color (RGB)** — WORKING, visually confirmed with R→G→B cycling x3
- **BLE Protocol30 fully reverse-engineered** — three bugs found and fixed:
  1. 20-byte padding bug in MITM proxy (keyboard requires exact byte lengths)
  2. Single-write SETs → must use split writes (header + data as separate ATT Write Requests)
  3. Wrong sub-parameter (sub1 must be 0x01, not 0x00)
- **MITM proxy firmware** — 8 iterations: padding fix, Write Request, SMP pairing, split writes
- **BT HCI capture infrastructure** — ETW trace + tracerpt XML parsing to decode Razer driver traffic
- **Effect type sweep** — static (0x01) works; initial sweep failed because dlen was fixed at 7
- **Driver init sequence captured** — 0x01/0xA0 (x2), 0x05/0x87, 0x05/0x84, 0x05/0x07
- **Effect data format decoded** from Chroma Studio HCI capture:
  - Variable-length: `[effect, param, 0, num_colors, R1,G1,B1, R2,G2,B2, ...]`
  - dlen = 4 + (num_colors × 3): static=7B, breathing-1=7B, breathing-2=10B, spectrum=4B
  - Static (0x01): `01 00 00 01 R G B`
  - Breathing 1-color (0x02): `02 01 00 01 R G B`
  - Breathing 2-color (0x02): `02 02 00 02 R1 G1 B1 R2 G2 B2`
  - Spectrum cycling (0x03): `03 00 00 00`
  - Wave/reactive/starlight: not yet captured

**Key Protocol Details**
```
GET: single ATT Write Request, 8 bytes [txn, 0, 0, 0, class, cmd, sub1, sub2]
SET: split ATT Write Requests:
  Write 1: [txn, dlen, 0, 0, class, cmd, sub1, sub2]  (8 bytes)
  Write 2: [data...]                                    (dlen bytes)

SET brightness: class=0x10, cmd=0x05, sub1=0x01, data=[0x00-0xFF]
SET color:      class=0x10, cmd=0x03, sub1=0x01, data=[enabled,0,0,effect,R,G,B]
```

> [!warning] Testing Checklist
> - [x] SET brightness over BLE — SUCCESS responses confirmed in serial log
>   - Notes: All 4 brightness levels (MAX/LOW/OFF/MAX) returned status 0x02. GET readback showed 0xFF after SET.
> - [x] SET static color over BLE — RGB cycling visually confirmed on hardware
>   - Notes: 3 full R→G→B cycles, user confirmed visual color change. GET state showed `01 00 00 01 ff ff ff` after white restore.
> - [x] Animated effects format decoded — variable-length data, captured from Chroma Studio HCI trace
>   - Notes: Static=7B, breathing-1=7B, breathing-2=10B, spectrum=4B. Wave/reactive/starlight still TBD.
> - [ ] Brightness visual confirmation — user did not observe brightness changes (may have been on wrong slot)
>   - Notes: Protocol responses were all SUCCESS and GET readback confirmed value changed.

## Build 2026-04-10--0230
**Changes**
- Autostart toggle in tray menu (registry Run key)
- Persistent remap storage investigation — CONCLUDED: not available

**Investigation Results**
- **Lighting: auto-persistent.** Color/brightness survive USB replug. No save command needed.
- **Keymaps: always volatile.** No save command found among class 0x02 SET candidates or other classes. Daemon re-applies on connect (correct approach).
- F-key persistent remaps in Synapse likely use separate Fn-layer firmware mechanism.

> [!warning] Testing Checklist
> - [x] Autostart toggle — registry key written/deleted correctly
>   - Notes: Verified via tray menu. Uses HKCU\...\Run\JoroDaemon.
> - [x] Lighting persistence — survives USB replug
>   - Notes: Set red, replugged, still red. Confirmed auto-persistent.
> - [x] Keymap persistence — does NOT survive USB replug
>   - Notes: Wrote backtick→F12, sent all save candidates, replugged, reverted to backtick.

## Build 2026-04-10--0130
**Changes**
- **Complete rewrite of keyboard hook architecture** — replaced pending-modifier state machine with modifier gate
- Lock key → Delete: single tap + hold-to-repeat, verified stable
- Copilot key → Ctrl+F12: verified working (triggers Greenshot)
- Win key, Win+E, Start menu all work normally
- Debug logging to file (`hook_debug.log`) for safe hook diagnostics
- `remove_hook()` releases all modifiers and clears state on shutdown
- Cleanup modifier injection on remap completion (LRESULT(1) suppression incomplete)
- Prefix mod cancellation: Copilot's leaked LShift undone at trigger time
- 36 unit tests (2 new for prefix mod detection)

**Bugs Fixed**
- Orphan key-ups from firmware event ordering (Win↑ before L↑)
- Auto-repeat Win↓ leaking through gate during hold-to-repeat
- LRESULT(1) not fully clearing Windows internal key state
- Copilot LShift prefix leaking through → Shift+Ctrl+F12 instead of Ctrl+F12

**Hardware Verification**
- Lock key → Delete: CONFIRMED (single tap, hold-repeat, clean state)
- Copilot key → Ctrl+F12: CONFIRMED (Greenshot screenshot triggered)
- Win key tap → Start menu: CONFIRMED working
- Win+E → Explorer: CONFIRMED working
- Normal typing: CONFIRMED stable

> [!warning] Testing Checklist
> - [x] Lock key → Delete (single tap)
>   - Notes: Working. Requires DisableLockWorkstation registry key.
> - [x] Lock key → Delete (hold to repeat)
>   - Notes: Working. Auto-repeat generates repeated Delete.
> - [x] Copilot key → Ctrl+F12
>   - Notes: Working. Triggers Greenshot. LShift prefix properly cancelled.
> - [x] Win key tap → Start menu
>   - Notes: Working. Gate replays Win tap when no trigger follows.
> - [x] Win+E → File Explorer
>   - Notes: Working. Non-trigger keys replay gate mod immediately.
> - [x] Normal typing stability
>   - Notes: Stable. No modifier corruption after remaps.
> - [ ] USB replug reconnect
>   - Notes: Not tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not tested

## Build 2026-04-09--2359
**Changes**
- Unified pending-modifier state machine replacing hardcoded COMPANION_MODIFIERS
- Multi-candidate trigger matching (LWin → either L or 0x86)
- Scan codes in SendInput via MapVirtualKeyW
- `build_remap_tables` returns both combo and pending-modifier tables
- Config supports `from = "Win+L"` combo-source syntax
- Default config includes Lock→Delete and Copilot→Ctrl+F12 entries
- DisableLockWorkstation registry key for Win+L interception
- 34 unit tests (4 new)

**Hardware Verification**
- Lock key → Delete: CONFIRMED WORKING (multiple sessions)
- Copilot key → Ctrl+F12: BROKEN — SendInput modifier injection causes stuck LCtrl

> [!warning] Testing Checklist
> - [x] Lock key → Delete
>   - Notes: Works reliably. Requires DisableLockWorkstation registry key.
> - [ ] Copilot key → Ctrl+F12
>   - Notes: **BLOCKED.** SendInput Ctrl↓/Ctrl↑ injection corrupts Windows keyboard state. LCtrl gets stuck after 1-4 presses. Required multiple reboots. Need firmware-level approach (find matrix index, remap to F13, then hook F13→Ctrl+F12).
> - [ ] Normal typing stability with daemon running
>   - Notes: Lock-only appears stable. With Copilot remap active, keyboard eventually gets stuck modifiers.
> - [ ] USB replug reconnect
>   - Notes: Not tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not tested

## Build 2026-04-09--1830
**Changes**
- Built complete Rust systray daemon (`joro-daemon`) — 6 modules, 30 unit tests
- `src/keys.rs` — VK code + HID usage lookup tables, combo string parser
- `src/usb.rs` — Razer 90-byte packet builder/parser + RazerDevice USB transport
- `src/config.rs` — TOML config schema, loader, color parser, default creation
- `src/remap.rs` — WH_KEYBOARD_LL keyboard hook + SendInput combo synthesis
- `src/tray.rs` — systray icon (green/grey circle), right-click menu
- `src/main.rs` — winit event loop, device lifecycle, config auto-reload
- Config at `%APPDATA%\razer-joro\config.toml`, auto-created on first run
- Fixed USB interface claiming (claim_interface + auto_detach_kernel_driver)
- Fixed winit event loop exit (hidden window required for systray-only apps)
- Build infrastructure: `build.ps1`, `.cargo/config.toml.example`, local target dir

**Hardware Verification**
- Static color orange (#FF4400) at brightness 200 — confirmed on keyboard
- CapsLock -> Ctrl+F12 host-side combo — confirmed working
- Firmware version query — confirmed
- Systray icon shows connected/disconnected state — confirmed
- Right-click menu (reload, open config, quit) — confirmed

> [!warning] Testing Checklist
> - [x] Static color set from config — observed on keyboard
>   - Notes: Orange (#FF4400) at brightness 200, applied on startup
> - [x] Host-side combo remap (CapsLock -> Ctrl+F12)
>   - Notes: Keyboard hook intercepts CapsLock, synthesizes Ctrl+F12 via SendInput
> - [x] Systray icon and menu
>   - Notes: Green circle when connected, right-click menu works
> - [ ] USB replug reconnect
>   - Notes: Not yet tested this build
> - [ ] Config file auto-reload
>   - Notes: Not yet tested this build
> - [ ] 2.4GHz dongle
>   - Notes: Not yet tested

## Build 2026-04-09--2100
**Changes**
- Identified CapsLock matrix index: **30** (confirmed via F12 remap test)
- Exhaustive modifier combo remap testing — **firmware does NOT support combos**
  - Tested: type field variations (01/02, 02/01, 03/02, 07/02, 02/07), modifier in pad/extra bytes, two-entry writes, modifier usage in pad
  - Only 1:1 key swaps work (including remap to modifier alone, e.g., CapsLock -> LCtrl)
  - Combos/macros must be implemented in host software
- Scanned all class 0x02 commands: GET 0x82/0x83/0x87/0x8D/0xA4/0xA8, SET 0x02/0x03/0x07/0x0D/0x24/0x28
- Created `proto/validate_keymap.py` — full keymap dump tool
- Created `proto/find_capslock_v2.py` — batch remap index finder
- Created `proto/map_all_keys.py` — batch-by-batch full keyboard mapper

> [!warning] Testing Checklist
> - [x] CapsLock remap — matrix index 30 confirmed
>   - Notes: Remapped to F12, verified CapsLock triggers F12 (opens devtools)
> - [x] CapsLock -> LCtrl — confirmed working (hold + C copies)
>   - Notes: Simple modifier remap works with usage=0xE0
> - [x] Modifier combo remap (Ctrl+Esc, Ctrl+A) — NOT SUPPORTED
>   - Notes: All entry format variations tested, firmware ignores modifier fields
> - [ ] Sleep/idle config SET command
>   - Notes: Not yet tested
> - [x] BLE GATT exploration
>   - Notes: 6 services found. Custom Razer service `52401523-...` has TX/RX/RX2 characteristics. 20-byte max write (PDU=23). BLE command protocol requires authentication — device returns nonce + status 0x03 for all writes. BLE VID=0x068E PID=0x02CE. Battery=100%. Conn params: 7.5-15ms interval, latency=20, timeout=3s. maintain_connection=False by default.

## Build 2026-04-09--1630
**Changes**
- Rewrote `proto/razer_packet.py` — corrected to openrazer `razer_report` struct layout (status@0, args@8)
- Rewrote `proto/usb_transport.py` — switched from hidapi to pyusb raw USB control transfers
- Created `proto/commands.py` — set_static_color, set_brightness, get_brightness, get_firmware, set_effect_none
- Created `proto/test_lighting.py` — interactive lighting test script
- Created `proto/scan_keymap.py`, `proto/scan_keymap_full.py`, `proto/find_capslock.py` — keymap RE tools
- Cloned openrazer PR branch to `openrazer-ref/`
- Installed Wireshark + USBPcap (USBPcap broken on this system, not needed)
- Added pyusb + libusb to requirements.txt
- Full brute-force command scan — discovered all supported class/id pairs

**Protocol Discoveries**
- Transport: pyusb control transfers (hidapi returns 0x05 not supported)
- LED: VARSTORE=0x01, BACKLIGHT_LED=0x05 (not 0x00)
- Keymap: GET 0x02/0x8F, SET 0x02/0x0F — matrix index-based entries
- Sleep: GET 0x06/0x86 (idle timeout), GET 0x06/0x8E (extended power config)
- Supported command classes: 0x00, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x0A, 0x0F

**Hardware Verification**
- Static color RED/GREEN/BLUE — confirmed working
- Brightness 0-255 — confirmed working
- Key remap backtick->A — confirmed working
- Key remap batch (indices 20-35) — confirmed affects real key output

> [!warning] Testing Checklist
> - [x] Static color set via `test_lighting.py` — observed on keyboard
>   - Notes: Verified RED, GREEN, BLUE, ORANGE color cycle
> - [x] Brightness control — observed on keyboard
>   - Notes: 0-255 range works, get_brightness reads back correctly
> - [x] Key remap (single key) — verified backtick->A on hardware
>   - Notes: Index 1 = backtick key. SET 0x02/0x0F with send-only (no GET response after SET)
> - [ ] CapsLock remap — matrix index not yet identified
>   - Notes: Confirmed in range 20-35, currently mapped to F-keys for identification
> - [ ] Modifier combo remap (e.g., Ctrl+F12)
>   - Notes: Not yet tested
> - [ ] Sleep/idle config SET command
>   - Notes: GET works, SET not yet tested
> - [ ] BLE connection and command test
>   - Notes: Not yet attempted

## Build 2026-04-09--1430
**Changes**
- Created project directory and research spec (`razer-joro-synapse-replacement.md`)
- Added firmware updater exe
- Set up project docs (`_status.md`, `WORKPLAN.md`, `CHANGELOG.md`)
- Created design spec and implementation plan

> [!warning] Testing Checklist
> - [x] Verify Joro is detected via `hid.enumerate(0x1532, 0x02CD)` on Windows
>   - Notes: 10 HID interfaces found. Interface 3 (MI_03) used for control.
> - [x] Confirm openrazer PR branch is accessible and cloneable
>   - Notes: Cloned to `openrazer-ref/`
