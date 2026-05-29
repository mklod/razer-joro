# Monitor brightness debug — what we learned the hard way

Last updated: 2026-04-21--1414

Companion to `MONITOR_BRIGHTNESS.md`. That doc is the design / current state. This doc is the debugging journal — what we tested, what we found, and the dead ends so we don't repeat them.

## The monitor

- Physical: **Samsung 49" Odyssey OLED G9 (S49DG91DSN)**, dual-QHD 5120×1440, OLED
- Scaler self-identifies in MCCS as `model(FALCON)` — that's the SoC/scaler firmware vendor, not the brand. Older code/memories use the "Falcon" name for this reason.
- Range reported by `GetMonitorBrightness`: `min=0, cur=N, max=50`. Confirmed in the MCCS capability string and via direct VCP 0x10 read. Genuinely 0..50, not a Windows API artifact.
- Advertised VCP codes (from caps string):
  ```
  vcp(02 04 05 08 10 12 14(05 08 0B 0C) 16 18 1A 52 60(01 03 04 11 12 0F 10) 62 8D FF)
  ```
  - 0x10 brightness, 0x12 contrast, 0x16/18/1A RGB gain, 0x52 active-control, 0x60 input source, 0x62 audio volume, 0x8D mute
  - 0x05 / 0x08 are factory-defaults restore codes — never tested writes; potentially useful as recovery, also potentially destructive.
  - 0xDC (picture mode) is **not** in the advertised list, even though `GetVCPFeatureAndVCPFeatureReply` returns `cur=8 max=3` for it. Treat any 0xDC reads as bogus.

## The reported symptom

Recurring user complaint: "brightness broken." Specifically:
- F8/F9 keypresses don't change physical brightness
- Windows generic OSD overlay drifts (Windows tracks the keypresses internally, so its number wanders away from the monitor's actual value)
- Sometimes: monitor reboots ("display PDO destroyed" / `0xC026258D` from dxva2)
- Triggered consistently after long idle → monitor DPMS-off → wake sequence

User correctly observed early on: "this feature has always been flaky after monitor power cycle" — so it's a longstanding G91SD↔Windows interaction issue, not a regression.

## Things we eventually proved

### The cache was the root cause

Original `BRIGHTNESS_STATE.last_target` cached the last value the daemon wrote, then derived deltas from it. When anything got out of sync (display-mode change invalidating the HMONITOR, monitor scaler dropping a write silently, daemon restart, etc.) the cache and the monitor's actual state diverged. From that point every "F8/F9 press" generated `stepped_write(cached_value → cached_value-5)` — a stream of absolute writes that had no relationship to where the monitor actually was.

Example collision: cache=50, monitor at 5, user presses F8. Daemon issues VCP 0x10 = 49, 48, 47, 46, 45 (five 1-unit absolute writes, 20ms apart). To the scaler each is a 40+ unit jump. Five of those in 100ms is what crashes/reboots the panel. We were generating the very pattern that triggered the bad behavior, then chasing the consequences with workarounds.

### Read-before-write fixes everything

Direct CLI testing on 2026-04-21, daemon dead, manual writes only (see MONITOR_BRIGHTNESS.md "Confirmed behaviour" section for the full session). Pattern: `GetMonitorBrightness` → compute target as absolute → `SetVCPFeature(0x10, target)`. Repeat:

| Test | Pattern | Result |
|---|---|---|
| 1 | 5 × 1-unit writes, 1s pauses | 5/5 landed |
| 2 | Single deltas of 2, 3, 5, 10, 20 | 5/5 landed |
| A | Single 4 → 50 (full range, +46) | 1/1 landed |
| 200ms round | 10 × ±5, 200ms gap | 10/10 |
| 100ms round | 10 × ±5, 100ms gap | 10/10 |
| 50ms round | 10 × ±5, 50ms gap | 10/10 |
| D 25ms round | 10 × ±5, 25ms gap | 10/10 |
| E 0ms round | 10 × ±5, no sleep | 10/10 |

Total: **51 successive absolute writes, zero drops, zero reboots, no monitor lock state observed.** Inter-press rates from 1s pauses down to back-to-back. Single deltas up to the entire range (46 units on a 0..50 scale).

The conclusions:
- Write size doesn't matter when read-before-write is honored. Single big jump = single small jump.
- The scaler tolerates rapid succession fine, as long as the values being written are coherent with current state.
- Windows API (`GetMonitorBrightness` + `SetVCPFeature`) is not the problem.
- "Lock state" we kept observing earlier in the session was almost certainly the artifact of cache-driven write storms, not an intrinsic monitor behavior.

## Dead ends — things that DIDN'T work and shouldn't be retried

### Verify-read after each ramp (added 2026-04-17, reverted same day)
`GetMonitorBrightness` 50ms after each write to detect silent drops. Worked as a diagnostic — caught the drops cleanly. But the extra read right after a write **caused monitor reboots** on the G91SD. Pre-existing comment in `brightness.rs` warned read/write interleaving is risky on this hardware; this confirmed it. Do not re-add post-write reads without a different mechanism.

### `WM_POWERBROADCAST` + `GUID_CONSOLE_DISPLAY_STATE` listener (added 2026-04-17, removed)
Spawned a worker thread, registered a hidden window for power events, called a warm-up on display-ON. Problem: Windows fires the event before the monitor is enumerable for DDC. Log showed `display wake — no DDC/CI monitors enumerable yet` immediately after the event — warm-up was a no-op. Even when the monitor became enumerable later, a separate `0xC026258D` would fire mid-ramp. The OS-level event isn't tied to scaler readiness; this whole listener was wrong abstraction.

### "Cold-path" warm-up via VCP 0x12 self-write (added 2026-04-17, removed)
Theory: a no-op write to a VCP code we know is writable would "kick" the channel awake. Tested directly with daemon dead: even a *real* contrast change (50 → 49) didn't unlock VCP 0x10 in the broken state. The "channel exercise" theory is false; VCP codes are independent.

### 3-consecutive-fail cache drop + 3-second transition window (added 2026-04-17, removed)
Once verify-read was caught failing 3 times, drop the cache and refuse brightness writes for 3 seconds, hoping the scaler would recover. Logs showed the recovery never happened in the 3-second window — the broken state lasted much longer. The 3-second number was speculative and unhelpful.

### Picture-mode-lock theory (proposed 2026-04-17, disproved)
Saw `VCP 0xDC cur=8 max=3` and theorized the monitor was in a Samsung-extended picture mode that locks brightness. Wrong on two counts: (a) `0xDC` isn't even in the advertised vcp list, so the read was bogus data; (b) tried writing `0xDC = 1` to "force standard mode" — write also dropped, so even if the theory had been right we couldn't recover from it. Don't trust readouts of unadvertised VCP codes.

### Power cycle / input switch / OSD touch as recovery
User tested all three during a "broken" state. **None unlocked the brightness register.** This rules out monitor-firmware-state explanations — power cycling clears the scaler's volatile memory. The lock state, when it appeared, was being held by something on the Windows side or by accumulated bad writes from the daemon, not by monitor firmware.

### My CLI batch probes triggering a monitor reboot (lesson learned 2026-04-21)
Early in the same-day testing I ran a batch of writes from CLI: `0x10 = 5`, `0x10 = 25`, `0x10 = 50`, `0x12 = 49`, `0x10 = 35` in quick succession. User reported the monitor rebooted shortly after. Lesson: even from CLI, chained writes when the daemon's own broken state may also be racing them is dangerous. **One operation per turn, with explicit user confirmation** is the right cadence.

## Things we didn't conclusively answer

- **What triggers the lock state in the first place?** We saw it correlate with monitor wake from DPMS, and we saw `0xC026258D` mid-ramp around the same time, but we never isolated the trigger from the observation. With the cache removed (Path 1 in the daemon refactor), we may simply never see the lock again — making the trigger question moot.
- **Does the lock genuinely self-release over time, or do certain Windows events release it?** During the long debug session both the elapsed time and various display events were happening simultaneously. Without the cache contaminating the test, future observations will be cleaner.
- **What does `0xC026258D` mid-write actually mean for the monitor?** Documented as "OS asynchronously destroyed the monitor handle" — usually a `WM_DISPLAYCHANGE` event. We never tied it to a specific external trigger (Nvidia driver, HDR auto-switch, EDID re-read, etc.). Out of scope.

## CLI test recipes (read-only / minimal-write)

For future debugging. Daemon should be killed first to avoid races. All paths assume the installed binary at `C:\Users\mklod\AppData\Local\razer-joro\joro-daemon.exe`. Output goes to `%LOCALAPPDATA%\razer-joro\daemon.log`, not stdout — grep the log for results.

Single read:
```bash
"$JD" brightness vcp 10 > /dev/null 2>&1
grep "VCP 0x10:" "$LOG" | tail -1
```

Capability string dump:
```bash
"$JD" brightness caps > /dev/null 2>&1
grep "caps:" "$LOG" | tail -1
```

Single write + verify:
```bash
"$JD" brightness vcp 10 = 35 > /dev/null 2>&1
sleep 1
"$JD" brightness vcp 10 > /dev/null 2>&1
grep "VCP 0x10:" "$LOG" | tail -1
```

Read-then-write characterization loop (the script we used in the 51-write characterization):
```bash
JD="C:/Users/mklod/AppData/Local/razer-joro/joro-daemon.exe"
LOG="C:/Users/mklod/AppData/Local/razer-joro/daemon.log"
read_brightness() {
  "$JD" brightness vcp 10 > /dev/null 2>&1
  grep "VCP 0x10:" "$LOG" | tail -1 | sed 's/.*cur=\([0-9]*\).*/\1/'
}
# Then in a loop: CUR=$(read_brightness); ... "$JD" brightness vcp 10 = $TARGET ...
```
