# Monitor brightness control — design, history, current behavior, open questions

Last updated: 2026-04-21--1414

> **2026-04-21 status:** all the workaround machinery described in "Fix layer 1–4" below has been **removed**. The current implementation is a single-function read-then-write per call. See `MONITOR_DEBUG_NOTES.md` for the empirical characterization (51 successful test writes, zero drops) that drove the rewrite, and the "Current implementation" section below for what the code actually looks like now.

## Monitor

- **Samsung 49" Odyssey OLED G9 (G91SD), model S49DG91DSN**
- Dual-QHD 5120×1440, OLED panel, integrated DisplayPort MST scaler
- Windows does **not** natively adjust its brightness via the standard brightness slider / keyboard media keys. The OS enumerates it as a "generic PnP monitor" with no brightness-control entry in Settings → System → Display. That's why we need DDC/CI instead.

> Scaler self-identifies in MCCS as `model(FALCON)`. That's the SoC/scaler firmware vendor's name, not the brand. Older code comments use "Falcon" for this reason — they're quoting MCCS, not making up a nickname.

---

## Why DDC/CI (and not something else)

| Option | Works? | Notes |
|---|---|---|
| Windows `SetMonitorBrightness` / IBrightness provider | ❌ | G9 isn't enumerated as an internal-integrated panel; no provider attaches |
| Media-key handling by Windows | ❌ | The HID Consumer Control BrightnessUp/Down reports are emitted by the keyboard but Windows has no monitor to route them to |
| Nvidia Display API | maybe | Vendor-specific; requires dependency on NVML/NVAPI; untested |
| Monitor OSD (joystick on back) | ✔ ground truth | Not programmable |
| **DDC/CI via dxva2.dll** | ✔ when it works | What we use |
| Shader-based software dimming | ✔ but wrong | Crushes HDR, washes OLED |

DDC/CI (Display Data Channel / Command Interface) is a low-bandwidth serial protocol piggybacked on the video link. Sends VCP (Virtual Control Panel) commands to the monitor's scaler. VCP code `0x10` = "Luminance" (brightness) per the MCCS spec.

---

## Current implementation — `src/brightness.rs` (post-2026-04-21 rewrite)

### Algorithm

Each `delta_all` / `set_all_percent` call:

1. `EnumDisplayMonitors` → `GetPhysicalMonitorsFromHMONITOR` → fresh `PHYSICAL_MONITOR` handle.
2. `GetMonitorBrightness(handle)` to read **actual current** brightness, min, max from the monitor itself.
3. Compute target as absolute value: `current ± (max-min) × percent / 100` for delta, or `min + (max-min) × pct / 100` for absolute set. Clamp to `[min, max]`.
4. `SetVCPFeature(handle, 0x10, target)` — single absolute write.
5. `DestroyPhysicalMonitors` — release handle.
6. Return.

No global state. No cache. No stepping. No retries. No verify-read. No transition windows. No power-event listener. No warm-up.

### Why this is correct now

Empirical testing on 2026-04-21 (51 successive writes, all rates from 1s pauses down to back-to-back, single-write deltas from 1 unit to 46 units = full range): **every single read-before-write absolute write landed cleanly. Zero drops, zero reboots, no lock state observed.**

The old `BRIGHTNESS_STATE.last_target` cache was the source of every prior failure mode. When the cache drifted from monitor reality (display-mode change, daemon restart, single dropped write), every subsequent press generated a stream of absolute writes far from the monitor's actual state — exactly the pattern that crashed/locked the scaler. Removing the cache removed every downstream failure.

### Trade-off

Each user keypress now does one extra DDC read (`GetMonitorBrightness`) before the write. On the G91SD that's ~30ms of additional latency per press. Test data shows this is invisible at human keypress rates and the scaler tolerates it indefinitely. Worth the one-shot cost to never desync.

### What was removed in this rewrite

| Removed | Reason |
|---|---|
| `BRIGHTNESS_STATE` global cache + `BrightnessState` struct | Root cause of every desync |
| `stepped_write` 1-unit-with-20ms-delay machinery | Single absolute writes work fine; 1-unit-stepping was unnecessary |
| `verify_and_resync` post-write readback | Caused monitor reboots; only existed to catch cache drift, which no longer exists |
| `consecutive_verify_fails` counter | Symptom-tracking for cache drift |
| `TRANSITION_DEADLINE` + `set_transition_deadline` + `in_transition_window` | 3s deferral after errors; defensive band-aid that never actually helped |
| `on_display_wake` + cold-path warm-up | Solved a problem that didn't exist when cache is gone |
| `src/power_events.rs` (entire module) | Listener for `WM_POWERBROADCAST` that fired before monitor was even enumerable. Useless. |
| `Win32_System_Power` + `Win32_System_SystemServices` Cargo features | Only used by `power_events` |

### Enumeration — `PhysicalMonitor::enumerate()`

1. `EnumDisplayMonitors` → Vec<HMONITOR>
2. For each HMONITOR: `GetNumberOfPhysicalMonitorsFromHMONITOR` + `GetPhysicalMonitorsFromHMONITOR`
3. `GetMonitorBrightness(hPhysicalMonitor, &mut min, &mut cur, &mut max)` — if this returns 0 we drop the monitor (not DDC/CI-capable). Otherwise we keep it.
4. Values go into `PhysicalMonitor { min, cur, max, … }`.

On the G91SD, `GetMonitorBrightness` returns `min=0, cur=<last>, max=50`.

### Stepped write — `stepped_write(monitor, start, target)`

```rust
let mut v = start as i32;
let end = target as i32;
let dir = if end > v { 1 } else if end < v { -1 } else { return true };
while v != end {
    v += dir;
    vcp_set(0x10, v)?;   // SetVCPFeature
    sleep(20ms);
}
```

**Why 1-unit steps at 20ms:** past sessions showed the G91SD's scaler full-reboots when DDC/CI brightness jumps by more than a few units in a single write. 1 unit every 20ms is in the tolerance band. A full sweep (0→50) takes ~1s.

### Delta flow — `delta_all(delta_percent)`

1. Acquire mutex
2. `ensure_state` — if `BRIGHTNESS_STATE` is None, enumerate and keep monitor index 0
3. Compute `new_val` = `last_target ± (max-min) × delta/100`, clamped to `[min, max]`
4. `stepped_write(last_target, new_val)` — if any write fails with a HANDLE-invalid error, drop the cache (`*guard = None`) so the next press re-enumerates
5. Update `last_target = new_val`

### Absolute set — `set_all_percent(pct)`

Same as delta but target is `min + (max-min) × pct/100`.

### Dispatch

- Base-layer `[[remap]]` entries with `from = "BrightnessUp"` / `from = "BrightnessDown"` compile to `Special(BrightnessDelta(+/-10))`
- `src/consumer_hook.rs` reads Consumer HID reports (usage 0x0070 = BrightnessDown, 0x006F = BrightnessUp) non-exclusively. On match it calls `crate::remap::dispatch_special_action(&Special(BrightnessDelta(10)))`, which calls `brightness::delta_all(10)`
- `src/remap.rs` `dispatch_special_action` is also called from the LL keyboard hook when a remap's output is a SpecialAction (e.g. Fn+F9 → BrightnessUp would route here too)

---

## History — what past sessions established

| Session | Finding | Lesson applied |
|---|---|---|
| Initial DDC work | Bare `SetVCPFeature(0x10, target)` with a single write caused the G91SD to full-reboot its scaler (black flash, source re-handshake) whenever the jump was more than a few units | Introduced `stepped_write` with 1-unit increments |
| Cache exploration | Calling `GetMonitorBrightness` between writes (to "sanity-check" before each step) also triggered reboots | Cached the PhysicalMonitor handle globally and stopped interleaving reads with writes |
| VCP-wide range attempt (earlier) | Switching the enumeration filter from `GetMonitorBrightness` to `GetVCPFeatureAndVCPFeatureReply` also triggered reboots on subsequent writes | Reverted; kept `GetMonitorBrightness` as the gate |
| Display-mode-change handling | HMONITOR/hPhysicalMonitor handles go stale on any WM_DISPLAYCHANGE event — `SetVCPFeature` returns error code `0xC026258D` ("OS asynchronously destroyed the monitor…") | stepped_write detects and returns false; delta_all drops the cached state so next press re-enumerates |
| Stale-handle spin | After BLE reconnect the daemon kept a cached HMONITOR that was no longer current — "brightness broken" complaints | Same re-enumerate-on-failure path catches it |

---

## This session (2026-04-17) — new observations

1. **`GetMonitorBrightness` on the G91SD returns max=50**, not 100. VCP 0x10 raw (`GetVCPFeatureAndVCPFeatureReply`) returns the same 50. The monitor's own OSD also shows "Brightness 15/50" — so the 0..50 range is genuine, not a Windows-API quirk.

2. **I attempted to widen the range via `vcp_get(0x10)` at enumeration time.** It returned `(cur=15, max=50)` — same as `GetMonitorBrightness`. No widening, so the code change was a no-op for this monitor. Code was reverted to avoid the historical "extra DDC read during enumeration can cause subsequent writes to reboot the scaler" risk.

3. **The critical desync finding (from user observation):**
   - Daemon log showed a clean ramp `15 → 20 → 25 → … → 50`, with `stepped_write` returning success every time (no DDC errors).
   - On the monitor's own OSD, brightness was still showing **15/50** — physically unchanged.
   - Windows' generic brightness overlay (the one that pops up when a brightness media key is pressed) showed **50/50** — because it tracks the HID report, not the monitor.
   - Three systems, three states: daemon thinks 50, OSD shows 15, Windows overlay shows 50.

4. **Recovery so far is just daemon restart:**
   - Killing and relaunching the daemon clears `BRIGHTNESS_STATE` → next press re-enumerates → `GetMonitorBrightness` returns the real 15 → subsequent writes start landing again.
   - Each time this has been reported (multiple sessions now), restart has fixed it. No code change has been the trigger for recovery.

5. **User's empirical trigger for the desync (key datum this session):**
   > "it seems to happen primarily after long sleep (monitor off, pc idle, joro sleep/disconnect). I come back to pc, turn on monitor, joro wakes/reconnects after keypress, I try to turn monitor brightness up and BROKEN"
   
   This is the first clear correlation we have.

---

## Hypothesis — post-wake DDC silent-drop window

When the G91SD exits DPMS standby, there appears to be a window (likely several seconds, possibly longer) during which:

- `GetMonitorBrightness` **succeeds** and returns the last-stored value (the NVRAM value — 15)
- `SetVCPFeature(0x10, v)` **returns success** (non-zero return code, no Windows error)
- The monitor's scaler **silently drops** or delays-into-oblivion the write
- Nothing persists to the monitor's brightness state

Meanwhile:
- Our cached `last_target` advances step by step through the ramp (15→20→25…→50)
- Windows' consumer-HID brightness overlay advances too, because Windows sees the HID report and has no other source of truth
- The monitor itself remains at the pre-wake value

Once the daemon's cache hits the reported max (50), further deltas are clamped to 50→50 no-ops. The user sees "brightness broken" because (a) the monitor didn't move, and (b) further Up presses have no effect at all.

### What would produce this pattern exactly

1. **Scaler warm-up period**: Monitor's DDC controller is running on a warm-boot path after DPMS exit; accepts packets but defers them until init completes, at which point old packets are discarded.
2. **Windows display driver reinitialization race**: The hPhysicalMonitor handle is valid from WDDM's perspective but the underlying I²C bus to the monitor is briefly non-functional. Writes are acknowledged at the Windows layer but don't reach the monitor.
3. **Monitor in HDR mode re-negotiating**: If the monitor had gone into a different color mode during sleep (SDR/HDR), the scaler may be holding the brightness register until the new mode negotiation settles.

Any of these would cause writes to succeed-but-not-persist, and reads to return the last cached NVRAM value.

### Why a restart fixes it

Restart happens several seconds to minutes after the bad window. By then the scaler has fully warmed up, and fresh enumeration + fresh writes land normally.

---

## Confirmed behaviour from instrumented recurrence (2026-04-17)

After the verify-read was deployed, we captured the next "brightness broken" recurrence in the daemon log. Hypothesis **confirmed**. Two distinct patterns showed up:

### Pattern A — intermittent silent drops (monitor half-working)

About every other ramp was silently dropped. The daemon's auto-resync kept `last_target` accurate, so re-presses did land:

```
brightness: ramping 35 -> 40 (range 0..50)
brightness: WRITE DROPPED wrote=40 monitor=35 — resyncing cache
brightness: ramping 35 -> 40 (range 0..50)    ← user press again; no drop log, so it landed
brightness: ramping 40 -> 45 (range 0..50)
brightness: WRITE DROPPED wrote=45 monitor=40 — resyncing cache
brightness: ramping 40 -> 35 (range 0..50)    ← user changes direction
brightness: WRITE DROPPED wrote=35 monitor=40
brightness: ramping 40 -> 35 (range 0..50)
```

This suggests the scaler's DDC/CI input can silently drop individual commands while still servicing others, even when no display-state event has fired.

### Pattern B — all-drop lockup after display-mode change

The cleaner smoking gun appeared after a `0xC026258D` error:

```
brightness: ramping 0 -> 5 (range 0..50)
brightness: stepped write 1 failed: The operating system asynchronously destroyed
    the monitor which corresponds to this handle... A display mode change occurs
    when windows sends a WM_DISPLAYCHANGE windows message to applications. (0xC026258D)
    — will re-enumerate next time
brightness: ramping 0 -> 5 (range 0..50)
brightness: WRITE DROPPED wrote=5 monitor=0 — resyncing cache
brightness: ramping 0 -> 5 (range 0..50)
brightness: WRITE DROPPED wrote=5 monitor=0 — resyncing cache
   ...[repeats 9+ times — every single press drops]...
```

The handle was re-enumerated after the 0xC026258D error (caller's existing behavior drops `BRIGHTNESS_STATE` on `stepped_write` failure, so next press re-enumerates). But the fresh handle was still pointing at a monitor that was in-transition — `GetMonitorBrightness` kept returning the pre-change value (`0`) on every readback, `SetVCPFeature` kept returning success with no error, and **nothing persisted**. User-facing effect: brightness completely unresponsive.

### What this tells us

- The 0xC026258D (display-mode-change event) is a **reliable precursor** to the all-drop state. We should treat it as a signal to defer writes, not just invalidate the cache.
- A single "silent drop" can be transient (Pattern A). Three-in-a-row is the threshold where recovery needs more than just a cache resync — the monitor is in a genuinely stuck state that needs time.
- `GetMonitorBrightness` readback *does* return the truth in the broken state (it reports the pre-wake value consistently). That's useful — it means our verify-read is the authoritative detection mechanism.

---

## Fix layer 4 — cold-path warm-up (2026-04-17 late evening)

User observation that changed the design: the typical broken sequence is

1. Long idle — display off, Joro BLE disconnected
2. Power monitor on (→ `power-events: display ON` fires, warm-up runs)
3. Tap Joro → BLE reconnects (several seconds of GATT activity, separate subsystem from DDC)
4. Wait for "inputs confirmed"
5. Press F9 → DDC write attempted

There can be **15–30 seconds between step 2 (warm-up) and step 5 (actual brightness write).** If the G91SD's scaler wasn't fully ready at step 2, the warm-up landed too early to unstick anything by the time the user's real press happened. (The BLE side is irrelevant — `brightness` talks DDC/dxva2 directly, never touches the keyboard.)

### Fix

Moved the warm-up into `ensure_state` in `src/brightness.rs`. It runs automatically on every "cold" brightness action — i.e. whenever `BRIGHTNESS_STATE` is `None`, which covers:

- Fresh daemon start (no prior brightness activity)
- After `on_display_wake` (cache explicitly dropped)
- After a `stepped_write` error (cache dropped by the error path)

Now the sequence is:

1. Long idle (cache from before still there, but stale)
2. Display ON event → cache dropped + early warm-up (may or may not help scaler)
3. BLE reconnect activity (irrelevant to DDC path)
4. User presses F9 → `ensure_state` sees cache is None → **fresh enumeration + VCP 0x12 no-op write** → then the real VCP 0x10 write

The pre-flight warm-up fires **just before** the brightness write, so timing matches whenever the user actually attempts the adjust.

Both warm-ups coexist: the OS-event one is a proactive best-effort; the cold-path one is the reliable just-in-time version. Log entries to look for:

- `power-events: display ON — triggering DDC warm-up` + `brightness: display wake warm-up on ... VCP 0x12 <- N` → proactive warm-up fired
- `brightness: cold-path warm-up on ... VCP 0x12 <- N` → just-in-time warm-up fired on the user's actual keypress

If the cold-path warm-up consistently prints but brightness still doesn't move on first press, 0x12-as-unstick isn't sufficient on the G91SD and we need a different mechanism — possibly writing to a Samsung-specific VCP code, or adding a 500 ms delay between warm-up and the real write.

---

## Fix layer 3 — WM_POWERBROADCAST listener + DDC warm-up (2026-04-17 evening)

The verify-read instrumented diagnostic correctly identified the silent-drop pattern but also caused display reboots on the G91SD — the historical "read/write interleaving is risky" warning in this file applies even to a single read 50ms after a ramp completes. That layer has been **reverted** in favor of a proactive, zero-read approach.

### What replaced it

- **New module `src/power_events.rs`** spawns a worker thread that owns a message-only Win32 window and registers for `GUID_CONSOLE_DISPLAY_STATE` via `RegisterPowerSettingNotification`. Windows fires `WM_POWERBROADCAST` → `PBT_POWERSETTINGCHANGE` on every display-state transition (on/off/dimmed).
- **On display-ON event** → call `brightness::on_display_wake()`. That function:
  1. Drops the global `BRIGHTNESS_STATE` cache (stale HMONITOR after wake is near-certain).
  2. Clears `TRANSITION_DEADLINE` — wake is a fresh start.
  3. Enumerates DDC/CI-capable monitors and, for each one, reads current VCP 0x12 (contrast) and writes it straight back. The value is unchanged from the user's perspective, but the DDC/CI command channel gets exercised on a VCP code the G91SD kept writable during the broken state (verified by direct CLI probe — we could write 0x12 even while 0x10 was frozen).
- Next user F8/F9 press then re-enumerates onto a monitor whose command path has just been pinged awake, with no stale cached state to confuse things.

### Why we expect this to help

- **No DDC reads from the main brightness path.** The only read is inside `enumerate()` (`GetMonitorBrightness`) which was already present and never linked to reboots.
- **The warm-up write uses a VCP code that was observed still-writable during the broken state.** We know `0x12` works when `0x10` doesn't, so this write should reliably land.
- **Event-driven rather than polling.** No extra work on non-wake keypresses — the overhead is confined to the handful of display-state transitions that happen per day.

### What we'll know from the next recurrence

The daemon log will show `power-events: display ON — triggering DDC warm-up` whenever Windows delivers the wake event, immediately followed by the warm-up result:

- `brightness: display wake warm-up on HMONITOR ... : VCP 0x12 <- N (no-op)` → warm-up landed, expect subsequent brightness to work
- `brightness: display wake warm-up on ... failed: <error>` → DDC channel is still jammed even for 0x12; the bug is deeper than hypothesized
- No `power-events: display ON` after wake → Windows isn't firing the event we registered for on this hardware; need a different signal (`WM_DISPLAYCHANGE` on the hidden window, or poll `EnumDisplayMonitors` for HMONITOR changes)

### Known limitations

- **Windows OSD overlay still drifts** when VCP 0x10 writes silently drop. Windows tracks its own brightness counter directly from the Consumer HID report (the raw `BrightnessUp`/`BrightnessDown` keyboard key) and has no way to learn the monitor actually didn't move. Fixing the OSD would require a HID filter driver to suppress the raw report — out of scope. Treat the monitor's own OSD as ground truth; the Windows overlay is cosmetic noise.
- **If the warm-up doesn't help**, the next likely signal we're missing is the actual monitor-input-handshake completion. `GUID_CONSOLE_DISPLAY_STATE` fires on *Windows's* display-state change, which is earlier than the scaler's actual readiness. Candidate follow-ups: add a 1-second delay before the warm-up write, or listen for `WM_DISPLAYCHANGE` as a secondary signal.

---

## Fix layer 2 — transition grace period + fail-3 cache drop (2026-04-17)

Added to `src/brightness.rs`:

- **`TRANSITION_DEADLINE: Mutex<Option<Instant>>`** — process-wide deadline. When set in the future, `delta_all` and `set_all_percent` log `monitor in transition window, skipping` and return immediately. 3-second grace window.
- **Deadline is set in two cases:**
  1. `stepped_write` returns false (typical cause: 0xC026258D / display-mode change)
  2. `verify_and_resync` observes 3 consecutive silent drops on the same cached monitor — at that point we assume the handle is addressing a stuck scaler and re-enumerating alone isn't enough
- **`BrightnessState.consecutive_verify_fails`** — reset to 0 on any successful verify, increments on each drop. The log line calls out the counter value so we can see drift patterns across sessions.
- Both deadline-setters also drop the cached `BRIGHTNESS_STATE` so the next press (after the 3s wait) does a fully fresh enumeration.

Expected behavior on the next recurrence:

| User action | Log entry |
|---|---|
| First F9 after display-mode change | `stepped write … failed: … 0xC026258D` → `entering 3s transition window … deferring brightness actions` |
| F9 during the 3s window | `monitor in transition window, skipping delta 10` |
| F9 after window expires | Fresh enumeration, writes land (hopefully) |
| Three silent drops without an explicit error | `WRITE DROPPED … (consecutive 1)` → `(consecutive 2)` → `(consecutive 3)` → `entering 3s transition window (3 consecutive silent write drops) …` |

This is still **reactive** — we detect the bad state and back off. The next step if this isn't enough is a **proactive** listener for `WM_POWERBROADCAST` + `GUID_CONSOLE_DISPLAY_STATE` so we know a display wake happened *before* a user press, not just in response to one.

---

## Diagnostic: verify-read after each ramp

To confirm the hypothesis we need data from an actual recurrence. Plan:

1. **Instrument `brightness.rs`** — after `stepped_write` completes, sleep 50ms (settle), call `GetMonitorBrightness` once, and compare the returned `cur` to the value we just wrote.
2. **If mismatch: log it** with the format `brightness: WRITE DROPPED wrote=N monitor=M — resyncing cache`. Also **auto-resync** — set `last_target` to the actual `cur` so the next press's delta is computed from reality, not from the drifted cache.
3. **If match: no log** (avoid flooding).

Risk trade-off:
- Adds **one** DDC read after each user-triggered ramp (not one per step). Our past learning is that reads *interleaved* with rapid writes can cause the scaler to reboot; a single read *after* the ramp settles should be safe (the known-bad pattern is read-write-read-write at high frequency, not write-write-write-read).
- If the single post-ramp read ever does cause a reboot on this monitor, we'll see the 0xC026258D error on the *next* press and our existing re-enumeration path covers it.

Expected outcomes on next recurrence:

| Observation in log | Conclusion |
|---|---|
| `WRITE DROPPED wrote=50 monitor=15` | Hypothesis confirmed — writes are silently dropped. Daemon now resyncs automatically; next press will attempt a real delta from the actual state. |
| `WRITE DROPPED wrote=50 monitor=50`, monitor OSD still shows 15 | The scaler is lying to us at readback too. Need a different verification path (e.g. read capability string, observe monitor OSD manually). |
| No mismatch ever logged, but monitor OSD still shows wrong value | The reads and writes are both hitting something that isn't the real monitor — e.g. a virtual/cached monitor slot. Need to look at GDI enumeration state across the sleep/wake. |

---

## Fix candidates (conditional on what the diagnostic shows)

### If writes silently drop post-wake
- **`WM_POWERBROADCAST` + `GUID_CONSOLE_DISPLAY_STATE` listener**: register for the power-notification event that fires when the console session's display turns on/off. On display-on, mark brightness as "warming up" for ~5s and defer any brightness actions (or queue them to fire after the warm-up window).
- Simpler fallback: **periodic verify-and-resync** — every few seconds, read `GetMonitorBrightness` and re-sync cache. Catches drift regardless of cause. (Downside: adds periodic DDC reads, which historically have been risky.)

### If the monitor lies on readback too
- Need an independent proof. Options:
  - Poll MCCS capability string (different codepath in the scaler) and compare.
  - Read a different VCP code that moves when brightness moves (e.g. backlight level `0x6B` if supported).
  - Add a hook to detect monitor's own OSD movements (not practical).

### If re-enumeration is required every time
- Periodically drop the cache proactively after long idle (e.g. if no brightness action in >60s, force re-enum on next press).
- Could also drop the cache on detected display-mode change (WM_DISPLAYCHANGE message in the hidden window we already have for the LL hook).

---

## Known constraints / things not to re-try

- **Don't** interleave `GetMonitorBrightness` with writes during a ramp. (Caused reboots historically; also makes each user press slower.)
- **Don't** use `GetVCPFeatureAndVCPFeatureReply` as the enumeration filter. (Historic reboots; current session confirmed no range-widening benefit on the G91SD.)
- **Don't** use `SetMonitorBrightness` instead of `SetVCPFeature`. The former is the high-level wrapper that goes through the Windows IBrightness provider which this monitor doesn't have.
- **Don't** suppress the raw Consumer HID report from reaching Windows (would require opening hidapi exclusively, which would break Synapse-style coexistence and possibly other apps). Accept that Windows' own brightness overlay will continue to pop up and drift from reality; the diagnostic is whether the monitor physically moves, not whether the overlay agrees.

---

## Open questions

1. What is the actual warm-up window on the G91SD after DPMS exit? (Needs measurement from diagnostic logs.)
2. Does the 0..50 range become 0..100 under any condition (SDR vs HDR, specific picture mode, Eco setting)? User's OSD currently shows `15/50`; worth checking if this matches the on-device brightness menu range.
3. Does the G91SD's scaler expose any VCP code that reports a "brightness effective" vs. "brightness commanded" split? (Few monitors do, but worth checking the capability string.)
4. Does pairing the verify-read with a short delay *before* the first write of a ramp (not just after) change behavior? This would effectively give the scaler a grace period on wake without requiring Win32 power-event handling.
