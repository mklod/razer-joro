# JORO FUNCTION — Authoritative Architecture Reference

> **Last updated: 2026-05-29--1510**
> This is the single source of truth for *how the Joro actually works* and *what our
> daemon does*. It supersedes scattered claims in `ARCHITECTURE.md`, `_status.md`,
> `WORKPLAN.md`, and the memory files wherever they conflict (contradictions are
> called out inline). Keep this short and current. Detailed RE journals stay in
> `FIRMWARE_RE.md` / `DONGLE_RE.md` / `MONITOR_BRIGHTNESS.md`.

---

## 0. Project goal

**Full Razer Synapse parity (and better) + the no-sleep firmware patch, so the user
never has to run Razer-brand software again.** The correct baseline for all testing
is: **our daemon running, zero Razer processes alive.** If a feature only works while
Synapse is alive, that's a daemon bug — replicate whatever state Synapse sets up
natively. Never propose "leave Synapse running" as a fix.

---

## 1. Hardware & transports

| | Value |
|---|---|
| Wired USB | VID `1532` PID **`02CD`** *(ARCHITECTURE.md's `02CE` is stale — wired is `02CD`)* |
| BLE | Advertised name `Joro`, paired PID `02CD`, Razer GATT service `0x52401523`, TX/RX char `0x52401524` |
| 2.4 GHz dongle | VID `1532` PID **`009C`** (Razer DA V2 X HyperSpeed receiver) |
| DFU bootloader | PID **`0x110E`** (wired only) |
| MCU | Nordic **nRF52** + SoftDevice BLE stack, ARM Cortex-M (Thumb-2) |
| Firmware version | `v1.2.2.0` |

Three ways to connect, **three different behavior profiles.** The single most important
fact in this whole project: **the transport changes what is possible.** See §A3.

---

# PART A — HOW IT ACTUALLY WORKS

## A1. The golden rule: firmware vs host-side

| Concern | Where it lives | Notes |
|---|---|---|
| **Key remaps (base layer)** | **Host-side** (LL keyboard hook) | Firmware base keymap lives in config-store flash `0xf2000`, **not** writable over BLE, **not** in the DFU-flashable regions. Synapse remaps host-side too. |
| **Hypershift (Fn+key)** | **Host-side** preferred (`fn_host_remap`); firmware path is USB-write-only | "BLE Hypershift" in Synapse is 100% host-side Windows interception. Firmware `class=0x02 cmd=0x0d` writes only over USB and only refresh on a transport cycle. |
| **MM/Fn primary toggle** | **HOST-SIDE** (see A2 — this is the big correction) | The firmware register exists but does **not** govern F-row output over BLE. |
| **Lighting / backlight** | **Firmware** (Protocol30) | Persists across transports + reboots. |
| **Battery** | **Firmware** (Protocol30 / heartbeat) | Std GATT battery char is frozen — don't use it. |
| **Sleep / wake behavior** | **Firmware** | Only fixable by FW patch (host idle cmds are NOT_SUPPORTED over BLE). |
| **Fn-key detection** | **Host-side** (reads firmware HID state) | Vendor HID `05 04 <state>` (BLE) / `04 <state>` (wired) / consumer `0x029D` (dongle). |
| **Monitor brightness** | **Host-side** DDC/CI to the *external monitor* | Completely separate subsystem; nothing to do with the keyboard. |

## A2. MM/Fn "Function Keys Primary" toggle — THE key clarification

This caused the most confusion in the docs. Resolved by direct test on 2026-05-29
(daemon stopped, zero host remaps):

- A firmware mode register **does exist**: Protocol30 `SET class=0x01 cmd=0x02 sub=00,00
  data=[mode,0]` (`mode=0x00` MM, `mode=0x03` Fn); `GET class=0x01 cmd=0x82`. It accepts
  writes over BLE and reads back the value you wrote.
- **BUT on the current firmware over BLE it is a NO-OP for F-row behavior.** With the
  daemon off, I set the register to MM, read it back as MM — and every F-key still
  emitted a **plain function-key VK** (F5=F5, F6=F6…), not media. The keyboard **always
  emits plain VK F1–F12 over BLE**, regardless of the register.
- **Wired:** the register returns `0x05 NOT_SUPPORTED`; F-row is hardcoded in wired FW.
- **Dongle:** dongle firmware flattens input to plain scancodes anyway (in Fn-primary
  mode), so the register is moot.

**Therefore: MM/Fn must be implemented HOST-SIDE to get Synapse parity.** Synapse does
exactly this — the keyboard sends plain VK F-keys and Synapse translates F5→Mute,
F6→VolDn, etc. on the host. The firmware register is at best a best-effort hint the
daemon may still write on connect, but it must **not** be depended on.

**2026-07-07 correction — the register is NOT a full no-op over BLE.** Live test (user):
with the register set to Fn-primary over BLE, **the Lock and Copilot keys emit NOTHING**
— the firmware's Win+L / Win+Shift+F23 macro composition is gated by the register even
though F-row emission is not. So the register has exactly one real effect over BLE:
enabling/disabling the Lock/Copilot macros. **Policy (implemented 2026-07-07): the daemon
pins the register to MM-primary on every connect and never writes Fn.** The user-facing
MM/Fn toggle is purely host-side (`device_mode` config → `build_remap_tables_for_mode`);
Lock/Copilot work in both toggle positions.

> Superseded claims: `project_fnmm_toggle_solved.md` ("flips firmware live, F5=Mute
> verified") and `ARCHITECTURE.md:185-196` MM column both assume the register switches
> F-row emission. The 2026-05-29 ground-truth disproves that for current BLE FW. The
> earlier "verified live" was almost certainly confounded by Synapse's filter driver
> being active, or by a since-flashed firmware change.

## A3. Per-transport capability matrix

| Capability | Wired USB (`02CD`) | Bluetooth LE | 2.4 GHz dongle (`009C`) |
|---|---|---|---|
| **Keyboard input** | Plain scancodes; vendor reports **unwrapped** (`04 XX`, `05 08 XX`). F1/F2/F3 reach the hook. | Plain VK F-keys; vendor reports **wrapped** (`05 04 XX`, `06 05 08 XX`). Consumer collection (Col03) readable. | **Flattened** to plain scancodes in Fn-primary mode (consumer/vendor/Fn silenced). In MM mode the dongle *does* forward consumer events. Wrapped form. |
| **Fn detection** | ✅ hidapi vendor Col05 `04 <state>` | ✅ hidapi vendor Col05 `05 04 <state>` | ⚠️ Only via consumer `0x029D`; RawInput consumer path **disabled** (dropped keys) → Fn+arrow Hypershift OFF |
| **MM/Fn register** | ❌ `0x05 NOT_SUPPORTED` | ⚠️ accepts+reads but **no F-row effect** (A2) | ⚠️ accepts but flattened anyway; needed only as a dongle pairing-mode flag for trigger remaps |
| **Base key remap** | Host-side (hook sees F1/F2/F3 too) | Host-side (F1/F2/F3 firmware-locked, unreachable) | Host-side, but Fn invisible + F-row flattened limits Hypershift |
| **Battery** | Protocol30 `0x07:0x80` arg[1] | Protocol30 `0x07:0x80` arg[1] | **Passive only** — heartbeat `09 31 <raw>` byte[2]; NEVER solicit-poll |
| **Lighting / control** | ✅ class `0x0F` (LED_ID `0x05`), raw USB control transfers | ✅ class `0x10` cmd `0x03`, split-write | ⚠️ class `0x0F` (LED_ID `0x00`); **dies after idle** (control channel dormancy) |
| **Firmware flash (DFU)** | ✅ wired only | ❌ | ❌ |
| **Sleep/wake** | No lag (cabled) | Worst: dropped keys after ~15 min idle on stock → fixed by no-sleep patch | Sleeps but wakes <100 ms |
| **Pairing** | n/a | Must be cleanly paired through Windows; hold `GattSession` MaintainConnection | Synapse-free pair proven (70-frame replay); bond lost on FW reflash |
| **Known RF bug** | — | ConnectionStatus flaps 5–10 s after connect (tolerate 3 strikes) | Dropped/repeating keys = **USB 3.x RFI** → move to a USB 2.0 port |

## A4. Per-key F1–F12 behavior (current firmware, over BLE)

| Key | Reality over BLE | Notes |
|---|---|---|
| F1, F2, F3 | ~~Firmware-locked BLE slot selectors~~ **OBSERVED 2026-07-07: emit plain VK_F1..VK_F3 over BLE (MM register, current no-sleep FW)** — they reach Windows, so they're host-remappable in principle. Cause of the change unknown (no-sleep FW patch? single-slot bond?). Slot-switching side effect not re-tested — remap with caution. | Old hard-limit claim outdated on current FW. |
| F4 | Plain VK_F4 (remappable) | *Not* a Win+Tab macro (that claim was retracted). |
| F5–F12 | **Plain VK_F5…VK_F12 over BLE, always** | The "MM mode → media/consumer" behavior in old docs does NOT happen at the firmware level on BLE. To get media keys, the daemon must translate host-side (A2). |
| F10/F11 | Plain VK over BLE; on wired the hardware backlight fires directly | — |

## A5. Hypershift (Fn + key)

- **Host-side (`[[fn_host_remap]]`)** — LL hook table gated on `FN_HELD`. Works on any
  transport *with the daemon running*, no USB needed. This is the daemon's primary path.
- **Firmware (`[[fn_remap]]`)** — `class=0x02 cmd=0x0d`, **USB-write only**; the runtime
  table only refreshes on a wired↔BLE transport cycle; persists across reboot. Targets
  the Hypershift (Fn) layer, not base.
- Dongle: limited because Fn detection there relies on the `0x029D` consumer path and
  the RawInput consumer subscription is disabled (dropped-keys bug).

## A6. Firmware: nature, flashing, what's patchable

- **Plaintext** ARM Thumb-2, ~158 KB, never encrypted. (The whole "encrypted / needs
  sacrificial keyboard" saga was a single off-by-one — chunk data starts at `args[9]`,
  D=9, not D=8.)
- **No cryptographic signature; per-chunk CRC not enforced.** Only integrity check is the
  openrazer XOR checksum at `report[88]`, recomputed per modified frame. Custom FW proven
  end-to-end (changed product string, booted reporting the change).
- **Flashing is WIRED-ONLY**, via `joro-daemon fw-flash-stock [--probe|--commit|--commit-mod]`
  (hidapi, 3-phase re-enumerating DFU: `00:04` on `02CD` → bootloader `0x110E` whole
  download → back to `02CD`). Interrupted DFU *before* commit is non-destructive.
  Recovery = `--commit` (flashes stock).
- **No-sleep patch #3 (current standing firmware, flashed 2026-05-19):** 3-byte change at
  region-03 `0x0e6d6` (`cmp r0,#1`→`b.n 0x0e6fa`) blocks both idle paths in dispatcher
  `0x0e6cc` → no link relax → no BLE wake lag. Artifact `assets/fwupdate_joro_nosleep.bin`
  (sha256 `121a40923b0b10ea`). **Tradeoff:** never power-saves (higher idle battery).
  **Reverts on any Razer FW update** (re-flash artifact). Reflash loses the dongle bond;
  the BLE bond survives. *(Verify against live hardware — no entry past 2026-05-19 confirms
  it wasn't reverted.)*
- **Patchable via flash:** sleep/wake (done), F1/F2/F3 BLE-slot hijack, Fn-press backlight —
  i.e. **code-level** behaviors in regions 02/03/04.
- **NOT patchable via flash:** the base keymap (incl. Lock→Win+L, Copilot combo) — it lives
  in config-store flash `0xf2000`, **outside** the DFU regions. Reachable only via Protocol30
  VARSTORE (class `0x0f`) or the host-side hook.
- **Keymap persistence:** class `0x02`/`0x03` writes are **RAM-only**; the persistent commit
  is a separate class `0x0f` VARSTORE path (40-byte records at `0xf2000`, gated by
  `*0x2000301e==2`). A transport cycle satisfies that gate, which is why "transport-cycle
  commits Hypershift." The daemon currently does the RAM write but **lacks** the class-0x0f
  commit.

## A7. Battery

| Method | Use it? |
|---|---|
| Std GATT Battery Service `0x180F` / char `0x2A19` | ❌ **Frozen** at last-charged value on this FW (showed 100% all day). Never use. |
| Protocol30 `class=0x07 cmd=0x80`, `pct = arg[1]*100/255` | ✅ **Primary for BLE/USB.** |
| Dongle heartbeat `09 31 <raw>`, `pct = (byte2*100+127)/255` | ✅ **Primary for dongle — PASSIVE only.** Never solicit-poll the dongle (RF bridge timeout `0x04` + input lag). |

## A8. Keyboard backlight (Protocol30 lighting)

- Effects via **BLE class `0x10` cmd `0x03`** (first data byte = effect: `0x00` off,
  `0x01` static, `0x02` breathing, `0x03` spectrum); brightness = `class 0x10 cmd 0x05`.
- **Per-transport encoding differs:** USB-direct and dongle use **class `0x0F`**
  (static `cmd 0x02`, brightness `cmd 0x04`), LED_ID byte `0x05` (USB) / `0x00` (dongle).
  *Verify wire bytes per transport before porting between backends.*
- Dongle lighting frames are byte-identical to Synapse's but **die after idle** (control
  channel dormancy, status `0x04`). Workaround: set lighting on wired/BLE — FW persists it
  onto the dongle. Avoid dongle `class=0x0F cmd=0x06` (disables backlight).
- **Cache was the bug, twice** (battery & monitor): do fresh read/write per call, no cache.

## A9. Monitor brightness via DDC/CI — SEPARATE SUBSYSTEM

This controls the **external Samsung G9 monitor**, not the keyboard. VCP code `0x10`
(luminance), real range **0–50**. Fresh `Get→compute→SetVCPFeature→Destroy` per call,
**no cache, no verify-read, no stepping** (all of those caused scaler reboots/desync —
do not re-add). Driven from F8/F9 via the consumer-HID hook (`0x006F`/`0x0070`). Windows'
own brightness OSD can drift from the monitor's true value — the monitor OSD is ground truth.

## A10. Razer contention (operational rule)

Running Razer processes (`RazerAppEngine`, `Razer Elevation Service`, `Razer Game Manager
Service 3`) **contend for the BLE/HID session** and silently break daemon LED + control +
mode writes (writes "succeed" at the WinRT layer but nothing changes; connection flaps).
**Kill them all; remove the `RazerAppEngine` autostart Run key.** The daemon depends on
zero Razer processes.

## A11. What is IMPOSSIBLE (and why)

| Want | Status | Why |
|---|---|---|
| Remap F1/F2/F3 on BLE | ⚠️ Possibly possible now | Old "firmware-locked slot selector" behavior gone on current FW — they emit plain VKs over BLE (observed 2026-07-07). Untested whether slot-switching still fires alongside. |
| Firmware-level MM/Fn that changes F-row over BLE | ❌ Not on current FW | Register doesn't change F-row emission over BLE (A2) — but it DOES gate Lock/Copilot macros, so it must stay MM. Do F-row translation host-side. |
| Lock/Copilot combos with firmware in Fn mode | ❌ | Register in Fn kills the Win+X macro composition entirely (2026-07-07). Daemon pins MM. |
| MM/Fn toggle on wired | ❌ | `0x05 NOT_SUPPORTED`; wired F-row hardcoded. |
| Flash-patch the base keymap (Lock→Delete via FW) | ❌ | Base keymap is in config-store `0xf2000`, outside DFU regions. |
| Write keymap over BLE | ❌ | Firmware accepts keymap writes only over USB. |
| Flash firmware over BLE/dongle | ❌ | DFU is wired-only. |
| Change lighting *while on dongle* after idle | ⚠️ Currently broken | Control-channel dormancy; solvable (keep-warm polling). |
| Fn+arrow Hypershift on dongle | ⚠️ Off | RawInput consumer subscription disabled (dropped-keys bug). |

---

# PART B — WHAT OUR DAEMON IMPLEMENTS (+ TEST STATUS)

Legend: ✅ working & tested · ⚠️ partial / known issue · ❌ broken / wrong impl · 🔲 not started

| Feature | How | Transport | Status |
|---|---|---|---|
| BLE connect + stay-connected | `windows` crate, GattSession MaintainConnection, 3-strike disconnect tolerance | BLE | ✅ (after killing Razer) |
| Battery reading | Protocol30 `0x07:0x80` arg[1]; 60 s poll on BLE/USB; passive heartbeat on dongle | BLE/USB ✅, dongle ✅ passive | ✅ (76% verified 2026-05-29) |
| Connection status in UI | `is_connected()` via WinRT ConnectionStatus | BLE | ✅ (was Razer contention, now fixed) |
| Base key remaps (LL hook) | `REMAP_TABLE` single→key/combo via SendInput | all | ✅ (user-confirmed working) |
| Trigger/combo remaps (Win+L→Delete, Win+Copilot→Ctrl+F12) | `TRIGGER_TABLE` gate state machine + kernel gate-mod release | BLE ✅, dongle ⚠️ | ✅ |
| Host-side Hypershift (Fn+key) | `FN_HOST_REMAP_TABLE` gated on `FN_HELD` | BLE ✅, dongle ⚠️(off) | ✅ on BLE |
| Fn detection | hidapi vendor Col05 `05 04`/`04` | BLE/wired ✅ | ✅ |
| Monitor brightness (F8/F9) | DDC/CI `brightness.rs`, consumer-hook capture | all | ✅ (51-write stress test passed) |
| Keyboard backlight (static/breathing/spectrum/brightness) | Protocol30 class 0x10 (BLE) | wired/BLE ✅, dongle ⚠️ | ✅ on wired/BLE |
| No-sleep firmware patch | 3-byte patch #3, `fw-flash-stock --commit-mod` | wired flash → all | ✅ (flashed 2026-05-19; verify live) |
| Synapse-free dongle pairing | 70-frame replay (`joro-dongle-pair`) | dongle | ✅ |
| **MM/Fn primary toggle (host-side)** | `remap::build_remap_tables_for_mode` injects MM F-row defaults under user remaps; rebuilt live on toggle. **Firmware register pinned MM always** (Fn register mode kills Lock/Copilot — 2026-07-07); toggle no longer writes it | BLE ✅ | ✅ re-architected 2026-07-07 (pending physical confirm) |
| Hook hardening (stuck-modifier fixes) | Gate autorepeat suppression; 50 ms firmware-burst fence on trigger/prefix matching; ACTIVE_COMBOS registry (key-up releases what key-down pressed, table-rebuild-proof); stuck-modifier watchdog (logical-vs-physical + stale gate/trigger, 1 s tick, 2-tick confirm); `hook_debug` config flag → hook_debug.log | all | 🔲 implemented 2026-07-07, needs live soak |
| Hypershift flash persistence | RAM write only; missing class-0x0f commit | USB | 🔲 |
| Dongle lighting keep-warm | not implemented | dongle | 🔲 |
| Fn+arrow on dongle (narrow RawInput) | disabled (dropped keys) | dongle | 🔲 |

### MM/Fn toggle — host-side, implemented 2026-05-29
Previously did nothing (the daemon only wrote a firmware register that no-ops over BLE).
Now a host-side daemon layer (`remap::build_remap_tables_for_mode`, called at all three
table-build sites + rebuilt live in the `set_device_mode_pref` handler). Design —
- Keep the keyboard in a known plain-VK state (it already emits plain VK over BLE).
- **Fn mode** = pass F5–F12 through as F-keys (+ user remaps on top).
- **MM mode** = daemon translates F5→Mute, F6→VolDn, F7→VolUp, F8/F9→monitor brightness,
  F10/F11→keyboard backlight, F12→PrintScreen — **underneath the user's custom remaps**
  (user remaps always win; never auto-edit `config.toml`).
- Because the keyboard emits plain VK over BLE, the old "F8/F9 no Win32 VK" / "F10/F11 not
  LL-catchable" problems disappear — every F-key arrives as a plain VK the LL hook sees.

### Deployment (resolved 2026-05-29)
Build with `cargo build --release` → output goes to `…\AppData\Local\razer-joro-target\release\joro-daemon.exe`
(per `.cargo/config.toml` `target-dir`). **Deploy step:** copy that exe over the autostart
path `…\AppData\Local\razer-joro\joro-daemon.exe` (the `JoroDaemon` Run key launches it at
login), then relaunch. Done 2026-05-29 with the MM/Fn toggle build.

### Battery charge status — OPEN (needs a capture before daemon work)
Battery **%** is solid (Protocol30 `0x07:80` arg[1] on BLE/USB; passive `09 31` byte[2] on
dongle; GATT `0x2A19` is frozen, never use). **Charge status (charging/plugged) is NOT known
on any transport.** openrazer's `0x07/0x84 = charging` does **not** apply — on Joro `0x07/0x83`/`0x84`
is the idle/power command and is NOT_SUPPORTED over BLE. No charge capture exists. Leads: the
undecoded bytes of the BLE `0x07:80` 4-byte response (`40 ?? c5 86`, only `arg[1]` decoded) and
the dongle heartbeat bytes 3–7. Cheapest test: the daemon already logs `raw=[…]` every 60 s —
plug power while on BLE and watch if those bytes change (caveat: USB-C plug may switch to wired).
