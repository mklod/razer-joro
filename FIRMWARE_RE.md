# Razer Joro — Firmware Reverse-Engineering Notes

Standalone reference for the Joro firmware RE + custom-FW pipeline.
Companion to the auto-memory (`project_*` notes); this is the durable
technical doc. Last updated 2026-05-19.

---

## 1. Platform

- **MCU:** Nordic **nRF52** running a **SoftDevice** (BLE stack). Evidence:
  peripheral map 0x40000000 (CLOCK/POWER/RADIO), GPIO 0x50000000, SCB/NVIC
  0xE000xxxx; 49 `svc` SoftDevice calls in region 03.
- **Flash write:** SoftDevice flash API — `svc #0x28` = page-erase,
  `svc #0x29` = write (funcs `0x00d40`/`0x00dd8`), plus a raw-NVMC fallback
  (`cpsid i` → NVMC.CONFIG `0x4001E504`/READY `0x4001E400` → `cpsie i`) for
  when the SoftDevice is disabled. App config/keymap records live at flash
  **`0xf2000`** (40-byte records) — OUTSIDE the DFU app regions.
- Transports: wired USB **PID 0x02CD**, bootloader **PID 0x110E**,
  HyperSpeed dongle **PID 0x009C** (DA V2 X HS receiver). BLE = Windows
  Bluetooth bond.

## 2. Firmware is PLAINTEXT (not encrypted)

The months-long "encrypted / hardware-only" belief was a **single
off-by-one** in chunk extraction. Correct per-chunk layout (verified by
vector-table + 44% bl-target-on-prologue self-consistency, and by a live
modify-flash that produced exactly the intended change):

90-byte report = `report[0:8]` Protocol30 header, `report[8:88]` = args
(80 B), `report[88]` = packet checksum, `report[89]` reserved.
For a `cmd=0x02` (class=0x10) chunk, the args are:

```
args[0:2] = chunk size (LE u16, 0x40=64; last 0x34=52)
args[2]   = region tag (0x02 / 0x03 / 0x04)
args[3]   = page    ┐ region-relative flash addr = (page<<8)|off
args[4]   = off     ┘ (chunks march contiguously: off 00/40/80/c0)
args[5:9] = 4-byte field, NOT an enforced CRC (see below)
args[9 : 9+size] = FIRMWARE DATA   ← "D=9". In whole-report terms the
                   data byte for region offset O is report[17+(O&63)].
```

**The off-by-one history (the central lesson):** an early extractor used
`args[8]` (D=8). One byte of misalignment per 64-byte chunk shredded
Thumb-2 alignment → the reconstructed image looked like high-entropy
noise → this was misread as "firmware is encrypted, only a hardware
SWD/glitch dump (sacrificial keyboard) can recover it." **Completely
wrong.** Rigorous cryptanalysis (entropy 6.6 not ~7.99, non-uniform
chi-sq, serial-correlated = *not a strong cipher*) plus brute-forcing D
against a hard invariant (valid Cortex-M vector table + Thumb-2
prologue density + 44% bl-target-on-prologue self-consistency) gave
**D=9**, later **proven by a live modify-flash**. Lesson: an off-by-N in
your own extraction perfectly mimics encryption — verify reconstruction
against an external invariant *before* ever concluding "encrypted".

There is **no enforced per-chunk CRC**: a modified data byte with
`args[5:9]` left exactly as captured is ACCEPTED and boots (proven by
the `Razer Joro`→`Razer Jor0` live flash, which also confirmed D=9 — a
wrong D would have changed the wrong byte). Only integrity =
**`report[88]` = XOR of report bytes `[2..88)`** (the standard openrazer
Protocol30 checksum, recomputed per modified frame) + a short
whole-image value in the `10:01` init. **No cryptographic signature
anywhere** — longest non-chunk DFU payload across all 4952 frames = 8
bytes (proven by `scripts/phase0_sig.py`).

### Image layout (D=9 region bins, `captures/joro_region_*.bin`)
| region | base | size | content |
|---|---|---|---|
| 0x02 | 0x7000 | 36 KB | app code (vector table: SP `0x20014a40`, reset `0x000274a9`) |
| 0x03 | 0x0000 | 64 KB | main app code (Protocol30, dispatch, idle/power) |
| 0x04 | 0x0000 | 54 KB | code + data/resources (USB descriptor strings here) |

Combined ≈ 154 KB. App flashes ≈ `0x27000`–`0x4E000`; bond/config store
≈ `0x9e000`–`0xf7000` (untouched by app DFU → BLE bond survives reflash;
dongle HyperSpeed bond does NOT survive).

## 3. DFU protocol — 3-phase re-enumerating flow

| frames | USB device | iface (wIndex) | Protocol30 |
|---|---|---|---|
| 0 | PID 0x02CD | 3 | `00:04` enter-update |
| → re-enumerates → | **bootloader PID 0x110E** | | |
| 1 … 4949 | PID 0x110E | **0** | `10:80` status, `10:01` init/erase, `10:02`×2470 chunk, `10:83`×2470 addr, `10:05`×3 commit |
| → re-enumerates → | back to 0x02CD | | |
| 4950 … 4951 | PID 0x02CD | 3 | `00:87` status, `00:0b` reboot |

- Device **reboots on the FIRST `10:05` commit** → trailing
  `10:05`/`00:87`/`00:0b` error with hidapi `0x3E3` (benign — flash already
  succeeded). Success signal = re-enumeration back to PID 0x02CD.
- **Safety:** an interrupted DFU *before* a successful program is
  non-destructive — bootloader boots the intact app (auto-recovers if no
  clean handshake; needs a manual power-cycle if a clean handshake
  occurred). Recovery from any bad flash = re-run the flasher with stock.
- Must use **hidapi** (not libusb/rusb — can't claim HID-bound ifaces on
  Windows). Implemented in `src/fwupdate.rs`.

## 4. Protocol30 command layer (region 03)

- 90-byte report: `[0]`status `[1]`txid `[2:4]`remaining `[4]`proto
  `[5]`dsize `[6]`class `[7]`cmd `[8:88]`args(80) `[88]`crc.
- **Class dispatcher `0x0bb88`**: request buf `0x2000b848`, `class=[req+6]`,
  `tbb`. class 2 → `0x0aa80` (RAM keymap), class 3 → `0x0b844`
  (keymap/profile), class **0x0f** → `0x0c0a4` (VARSTORE persist), etc.
- **Keymap persistence:** keymap writes (class 2/3, cmd 0x8d/0x0d) only
  touch a **RAM cache**; persistence is the class-0x0F VARSTORE commit
  (records → flash `0xf2000`, 40-byte). Base keymap is NOT a flat ROM
  table in the flashed regions — it's config-store driven. ⇒ firmware
  key-remap needs the config path or code RE, not a table patch.
- Idle/power command `class=0x07 cmd=0x83`(set)/`0x84`(get) — Synapse
  uses these over the **dongle/USB only**; **NOT_SUPPORTED over BLE**
  (status 0x05). The daemon's old `set_idle_time` used `cmd=0x03` (wrong
  for Joro — Joro uses `0x83`).

## 5. Sleep / idle machinery (the wake-lag target)

- `0x06cf4` — main-loop idle: `svc #0x41` (≈ `sd_app_evt_wait`). Normal
  µs idle, wakes on any event. **NOT the lag. Do not patch.** Only caller:
  superloop `0x03222`.
- **`0x0e6cc`** — idle-state dispatcher. `bl 0x0a630`; state = `bl 0x12638`
  (region-02 extern):
  - state==1 → `bl 0x0a77c` → tail `b.w 0x0a7ec` (**lighter idle path** —
    complex: own `sev/wfe`, writes `0x40010600`, `bl 0x12720`)
  - state==2 → `bl 0x0a8ec` → tail `b.w 0x0a970` (**deep power-save**)
  - else → return
- `0x0a970` — deep power-save: relax `bl` cluster (0x12d40,0x12e34,0x50fc,
  0x13a44,0x4160,0xffff754a,0xffffd7b4,0x4320,0x3482) → `sev;wfe;wfe` →
  arms `*0x2000305c` = `0x1f4`(500)@`0x0a9de` or `0x7d0`(2000)@`0x0aa06`
  → `bl 0x138d0` (region-02 timer) → sleepflag `*0x200038e0=1` → clears
  state byte `[0x2000b820+0x23]`. `0x2000305c` has ONE writer (here), no
  literal reader ⇒ a timer PERIOD passed to `0x138d0`, role unconfirmed.

### Empirical results (2026-05-19) — see `project_phase4_sleep_re`
- Repro: STOCK, ~15 min BLE idle → `asdfasdf`→`asdf` (~1.5 s+/~4 chars).
  Deterministic; threshold ~10–15 min (sub-5-min idle never sleeps).
- **Patch #1** (consts `0x1f4`/`0x7d0` → `movw r0,#0xffff`): no real change.
- **Patch #2** (`0x0e6cc` state==2 path `b.w 0x0a970` → `bx lr;nop`,
  i.e. never enter deep power-save): **no change, == stock.**
- ⇒ **The lag is NOT specific to the `0x0a970` path.** RE of `0x0a7ec`
  (state==1) shows **both idle paths call the SAME shared relax-action
  cluster**: `0x12d40`, `0x12e34` (region-02 externs), `0x50fc`,
  `0x4160`, `0x3482`, `0x327c`. That is *why patch #2 did nothing* — it
  blocked state==2 but state==1 → `0x0a7ec` runs the identical actions.
  **The lag is these shared relax actions, not the path/timer.**
  In-region ones disassembled:
  - `0x3482`: loops `bl 0x69bc(idx,1,2)` over indices 0x15..0x20 →
    GPIO/LED power-down config (the visible LEDs-off-on-sleep).
  - `0x4160`: iterates a 7-entry table @flash `0x0004592c` (+0x38) →
    zone/matrix teardown.
  - `0x0327c`: `mov.w r1,#0xf3000; bl 0xffff754a` → writes config flash
    (~`0xf3000`, save-state-on-sleep).
  - `0x50fc`: sub-dispatcher (bl 0x48d4/0x4fdc/0x4a38/0x4a74/0x4ca8).
  None is obviously the BLE conn-param relax → that is likely in the
  region-02 externs `0x12d40`/`0x12e34` or SoftDevice-level. **Next:
  RE 0x12d40/0x12e34 (need unified flash map for region-02), and/or
  the user's fast-decrease-test on the idle threshold once located.**
  Open RE target — kill the shared relax (NOP the BLE-relax action in
  BOTH paths) OR find+disable the idle gate.

### ✅ WAKE-LAG SOLVED 2026-05-19 — patch #3
The idle threshold was buried in an impractically deep delegation chain
(`0x0e6cc→0x12638→…`), so instead we blocked the dispatcher itself.
**Patch #3 (the solution):** at region-03 **`0x0e6d6`**, `cmp r0,#1`
(`01 28`) → **`b.n 0x0e6fa`** (`10 e0`). `0x0e6cc` still calls
`bl 0x0a630` + `bl 0x12638` (side-effects preserved) then falls
straight to the do-nothing return `0x0e6fa` (`pop {r4,pc}`) — so
**neither idle path (`0x0a7ec` lighter / `0x0a970` deep) is ever
entered → the shared relax cluster never runs → no BLE link relax →
no wake lag.** Verified: STOCK ~15 min BLE idle dropped ~4 chars /
1.5 s+; patch #3 = **instant, zero drops**, BLE connectivity intact.
- Net change vs stock = **3 bytes** (2 instr + that frame's `pkt[88]`).
- Artifact: **`assets/fwupdate_joro_nosleep.bin`** (sha256
  `121a40923b0b10ea`) = the permanent custom firmware. Flash via
  `joro-daemon fw-flash-stock --commit-mod` after rebuilding with this
  blob (or keep it as the standing `fwupdate_mod_replay.bin`).
  `scripts/make_sleep_patch3.py` regenerates it from clean stock.
- **Tradeoff (accepted):** the keyboard never enters its power-save →
  higher idle battery draw. That was the explicit goal (responsiveness
  over battery).
- **Caveats:** any Razer FW update reverts this (re-flash the artifact);
  any reflash loses the dongle HyperSpeed bond (BLE bond survives).
  Recovery from a bad state = `fw-flash-stock --commit` (stock).

### Next experiment (planned): the "decrease-then-confirm" fast test
Instead of proving "no sleep at 15 min" (slow), patch a candidate
timeout *DOWN* (e.g. → ~1 min / ~10 s) and idle briefly: if the
lag/sleep now triggers *sooner*, that constant IS the timeout (units
learned) → then set it huge to disable. Decisive in ~1 min/iteration.
Requires first finding the *right* candidate via §6 RE.

## 6. Open RE targets

1. Trace `0x12638` (region-02) — what state value does prolonged idle
   yield (1 vs 2)? Determines which idle path actually runs at ~15 min.
2. RE `0x0a7ec` (state==1 lighter-idle path) — its own idle timer /
   the `bl 0x12720` and `0x40010600` writes; likely the real lag path.
3. Hunt `sd_ble_gap_conn_param_update` (SoftDevice SVC) + a
   `ble_gap_conn_params_t` struct (min/max interval u16, slave_latency,
   timeout) — fast vs relaxed param sets + the idle gate selecting slow.
4. F1/F2/F3 BLE-device-switch hijack (Phase 4b) — code patch target.
5. Lock key = firmware-level, user never uses it → ideal first
   firmware key-remap target.

## 7. The custom-FW pipeline (PROVEN, recoverable)

- **Flasher:** `src/fwupdate.rs`, hidapi 3-phase. CLI:
  `joro-daemon fw-flash-stock [--probe | --commit | --commit-mod]`
  (dry-run default; `--probe` = transport test, stops before erase, zero
  risk; `--commit` = stock; `--commit-mod` = `assets/fwupdate_mod_replay.bin`).
- **Blobs:** `assets/fwupdate_stock_replay.bin` (verbatim captured DFU,
  byte-identical to an accepted Synapse session — the recovery image);
  `assets/fwupdate_mod_replay.bin` (current modified image).
- **Patch recipe** (`scripts/make_strflip.py` / `make_sleep_patch*.py`
  are templates): locate target bytes *by content* in the D=9 region
  bin → map region-offset → captured `10:02` chunk (tag/page/off,
  data@`args[9]`) → patch bytes in a copy of the stock blob → recompute
  ONLY that frame's `report[88]` (XOR [2..88)) → leave `args[5:9]` →
  write mod blob → `cargo build` (blob is `include_bytes!`) → flash
  `--commit-mod`. Self-verify patched bytes by content before writing.
- Reflash preserves the **BLE bond** (config region outside DFU) →
  safe to iterate over BLE. Dongle bond is lost on reflash (accepted).

## 8. Key addresses (region 03, D=9, base 0)

| addr | what |
|---|---|
| `0x0bb88` | Protocol30 class dispatcher (class=[0x2000b848+6]) |
| `0x0c0a4` | class-0x0F VARSTORE record store (flash `0xf2000`, 40-byte) |
| `0x0aa80` / `0x0b844` | RAM keymap / keymap-profile handlers |
| `0x06cf4` | main-loop `svc #0x41` idle (DO NOT patch) |
| `0x0e6cc` | idle-state dispatcher (state via `bl 0x12638`) |
| `0x0a7ec` | lighter idle path (state==1) — **prime lag suspect** |
| `0x0a970` | deep power-save (state==2) — ruled OUT as lag cause |
| `0x0a9de`/`0x0aa06` | `mov.w r0,#0x1f4`/`#0x7d0` (the patched consts) |
| `0x0e6ea` | `b.w 0x0a970` (patch#2 site, now `bx lr;nop`) |
| `0x00d40`/`0x00dd8` | NVMC flash write/erase (`svc 0x28`/`0x29`) |
| RAM `0x2000b848`/`0x2000b850` | Protocol30 request / response bufs |
| RAM `0x2000305c` | deep-sleep timer period (1 writer, role unconfirmed) |
| RAM `0x200038e0` | sleep flag |

---

## 8b. Unified flash layout (cross-region resolution) — SOLVED

Region bins are separate DFU payloads; cross-region `bl`s from region 03
(`0x12d40`, `0x13a44` fwd; `0xffff754a` etc. bwd) only resolve once the
absolute layout is known. Found by the same self-consistency metric as
D=9 (`scripts/unified_map.py`, tries the 6 orderings, scores region-03
bl-target-on-prologue across the combined image):

**Layout = `region02 ‖ region03 ‖ region04` contiguous; region03 base
`0x9000`** (region02 = 36 KB = 0x9000, then region03 64 KB, then
region04). 43 % bl-resolve (vs 25–34 % for wrong orders) and the key
externs land on prologues:

| region-03 extern | resolves to |
|---|---|
| `0x12d40` | region04 +`0x2d40` (prologue ✓) |
| `0x12e34` | region04 +`0x2e34` |
| `0x13a44` | region04 +`0x3a44` (prologue ✓) |
| `0xffff754a` | region02 +`0x54a` |
| `0xffffb4c4` | region02 +`0x44c4` (prologue ✓) |
| `0xffffd7b4` | region02 +`0x67b4` |

Combined-image addressing: `r02 ∈ [0,0x9000)`, `r03 ∈ [0x9000,0x19000)`,
`r04 ∈ [0x19000, …)`. `scripts/disasm_r04.py` disassembles region-04
with bl targets resolved into this map.

**Shared relax-action externs decoded** (called by *both* idle paths —
the wake-lag mechanism): region04 `0x2d40` & `0x2e34` are **timer
reconfiguration** — peripheral `0x4001a504` (nRF52 TIMER/RTC-class)
config writes, `×0x3e8`(1000) unit-scaling, structure iteration with
`0x1f4`(500)/`0x3e8`(1000) constants, calling region-02 helpers
(`r02+0x4c8/0x1434/0x1238`). These reconfigure timers when entering
idle (affecting wake latency). The actual BLE `sd_ble_gap_conn_param`
relax (if SVC-based) is deeper — in those region-02 helpers or
SoftDevice. **NOT yet pinned to a single patchable site; the full
relax/idle path is a deep multi-region timer+BLE state machine.**
Next: hunt the idle-inactivity *threshold* (the ~15-min counter reset
on key input, compared to a constant) — the clean knob for the
"decrease → fast-confirm → then disable" test, rather than fully
RE-ing the relax cluster.

## 8c. Strategy — FW patches vs. the daemon workaround layer

The daemon is a large host-side workaround engine for firmware-locked
keys (WH_KEYBOARD_LL modifier-gating, `consumer_hook` hidapi reader,
fn/mm-mode juggling, injection-tagging, `DisableLockWorkstation`
registry hack). Custom FW (now proven) can fix these at the source —
but FW is **static** (wired flash to apply; reverts on Razer FW update;
reflash drops the dongle bond) while the daemon is **dynamic** (instant,
no-flash, live-reconfigurable per user). ⇒ **division of labor, not
replacement:**

- **FW fixes the structural locks only** (change ~never, so "wired flash
  to apply" is a non-issue): F1/F2/F3 BLE-switch (host-impossible),
  sleep/wake (done — host-impossible over BLE), Fn-press white backlight
  (pure FW), Lock-as-SAS & Copilot & F-row-consumer (host-only-fragile).
  Goal: make these keys emit plain, predictable, interceptable scancodes.
- **Daemon stays the dynamic layer** for arbitrary per-user
  `[[remap]]`/`[[fn_host_remap]]` — baking those into FW would be wrong
  (every tweak = a wired flash).
- **Payoff:** unlocking the keys in FW lets the daemon remap a *clean*
  surface → deletes `consumer_hook`, fn/mm juggling, injection-tagging,
  the SAS/registry hack, modifier-gating — without losing live
  reconfigurability.
- **Caveats:** Razer FW update reverts all patches (one wired re-flash
  of the combined image); reflash loses dongle bond (BLE survives;
  Phase 5); each patch is its own bounded RE task.
- **CORRECTION 2026-05-19 (verified):** distinguish two classes —
  - **Flashable CODE behaviors** (patchable via our pipeline): sleep ✓,
    F1/F2/F3 BLE-switch (radio/code logic), Fn-press backlight (LED
    code). Behavioral code in r02/r03/r04.
  - **Config-store keymap entries** (NOT in flashed regions — at flash
    `0xf2000`, outside DFU; can't be FW-flash-patched): per-key remaps
    incl. **Lock→Win+L**, Copilot combo. Only via Protocol30
    keymap/VARSTORE (Phase 1, parked) or the daemon host-side hook.
    Proven: searching all flashed regions for the keymap-entry pattern
    `02 02 <mod> <usage>` and any `02 02 ?? ??` table = **zero hits**
    (consistent with the config-store architecture). So "Lock→Delete via
    FW flash" is NOT viable (the §8c earlier wording assumed it was code
    — wrong). F1/F2/F3 IS code (only-FW-can-fix-it) → the real target.
- **Combined-image plan (stepwise, prove-CAN-first):** accumulate ONE
  patch at a time into a single modded image, flash+verify each,
  re-baseline after each proven step:
  `sleep ✓ → F1/F2/F3 → Lock → Copilot → Fn-backlight → F-row`.
  Never all at once; every step recoverable via `--commit` stock.

## 9. RE Process — how the specs above were obtained (replicable)

Goal: get plaintext Joro firmware and the ability to flash modified
firmware, **without any Razer software at runtime**. Only Razer asset
used: a *one-time* USBPcap of Synapse doing things (capture-once →
replay-forever; consistent with the no-runtime-dependency rule).

### 9.1 Capture the firmware via Synapse's FW updater (one-shot)
1. Keyboard switch → **WIRED**, USB-C to PC (wired Joro = PID 0x02CD).
2. Run `scripts/start_fw_capture.ps1` — launches USBPcap on **all three**
   roothubs (`\\.\USBPcap1/2/3`) into `captures/fw_update_uN.pcap`
   (the device can be on any roothub; only the populated one grows).
   Requires USBPcap installed (`C:\Program Files\USBPcap\USBPcapCMD.exe`).
3. Trigger Synapse's `Joro_02CD_FirmwareUpdater` (it auto-fetches/installs
   when a FW update is offered). It re-flashes over wired USB.
4. Stop USBPcap; the populated file (`fw_update_u1.pcap`, ~2 MB / ~23 k
   pkts) is the capture. **One-shot per FW version** — once the keyboard
   is current the updater won't re-run. Keep the pcap forever; it is both
   the firmware source AND the recovery image.
- Razer Protocol30 rides HID `SET_REPORT(Feature)`: setup bytes
  `bmReq=0x21 bReq=0x09`, `wValue=0x0300`, `wLength=90`; the 90-byte
  report follows. All extractor scripts find frames by scanning for
  `21 09` + `wLength==90`.

### 9.2 RE the FW-updater EXE (understand the protocol; check host crypto)
The updater is a wrapped installer. Unpack chain:
- 17 MB PE → trailing overlay archive → inner ZIP →
  `CustomerFWU2Point5.exe` (.NET WinForms orchestrator),
  `FWUpdaterDLL.dll` (native x86 C++ MFC — the DFU API),
  `Ry_Online_Update_Dll`, localizations, `update_config.ini`
  (has `encryption_en=1` — a *template default*, NOT applied to Joro;
  do not trust it). Extracted under `captures/fwu_extract/zip_contents/`.
- `scripts/dotnet_reflect.py` (pythonnet/dnfile) reflects the .NET
  `DeviceInterface` → semantic names: `EraseFW`↔`class=0x10 cmd=0x01`,
  `ProgramFW`↔`0x10/0x02`, `VerifyFW`↔`0x10/0x80`,
  `EnterDevMode`↔`0x00/0x04`, `DevReset`↔`0x00/0x0b`, `SendCmd`=generic.
- `scripts/disasm_fwudll.py` (capstone x86, image base `0x10000000`)
  disassembles `DFUProgram`/`EnterDeviceMode`/`DFUErase`: `DFUProgram`
  **`memcpy`s the caller buffer verbatim** into the packet — no XOR/AES/
  transform (only `xor eax,ebp` = MSVC `/GS` canary). No AES S-box / SHA
  / P-256 / CryptoAPI in any binary. ⇒ host does zero crypto; it streams
  the bytes as-is. (At the time this *seemed* to imply "device decrypts";
  it actually meant "nothing is encrypted" — see §12.)

### 9.3 Extract the firmware image from the pcap
- `scripts/extract_regions.py`: for each `cmd=0x02` frame, read
  `args[0:2]`=size, `args[2]`=tag, `(args[3]<<8)|args[4]`=addr,
  `args[9:9+size]`=data (**D=9**); group by tag → per-region images
  `captures/joro_region_{02,03,04}_at_0x….bin`.
- Validate the offset, don't assume it: `scripts/find_data_offset.py` /
  `verify_reconstruction.py` brute D=0..12 and score each by (a) a
  plausible Cortex-M vector table at the region-0x03 base, (b) Thumb-2
  prologue density, (c) `prove_plaintext.py`'s **bl-target-lands-on-a-
  detected-prologue** rate (~44% for the true D, ~0% for noise).
  `decisive_verdict.py` / `cryptanalyze_fw.py` independently rule out
  encryption (entropy/chi-sq/serial-corr) so a "noise" result is known
  to be misalignment, not a cipher.

### 9.4 Decode the DFU protocol + the 3-phase re-enumeration
- `scripts/phase0_sig.py`: enumerate every non-`0x10:0x02` frame →
  the full command set (§3) and the proof that the longest non-chunk
  payload is 8 B (no signature).
- The re-enum was found empirically: a naive single-handle flasher died
  at frame 3 (`10:01`) with a USB I/O error; a PnP enumeration check
  showed the device had become bootloader **PID 0x110E**.
  `scripts/dfu_transition.py` parses the USBPcap per-packet header
  (device address @ offset 19, setup `wIndex`) for every Protocol30
  frame → reveals the exact 3 device/iface phases in §3.

### 9.5 Build the flasher; prove custom FW
- `src/fwupdate.rs` — hidapi (NOT libusb: Windows won't let libusb claim
  HID-bound interfaces), 3-phase, modes `--probe|--commit|--commit-mod`.
- `scripts/gen_fwupdate_blob.py` → `assets/fwupdate_stock_replay.bin`
  (the 4952 captured frames verbatim; byte-identical to an accepted
  Synapse session per `validate_fwupdate.py`). This is the recovery
  image and the modify base.
- Prove non-destructively first: `--probe` runs phase A + opens the
  bootloader + sends status frames and **stops before `10:01` erase**
  (interrupted-before-program auto-recovers). Then `--commit`
  stock→stock round-trip (proves flasher+recovery). Then a 1-byte
  modify (`scripts/make_strflip.py`: `Razer Joro`→`Razer Jor0`) +
  `--commit-mod`: device boots reporting the changed USB product string
  ⇒ **modification works, no enforced CRC, D=9 confirmed**.

## 10. Tooling inventory (`scripts/` unless noted)

| file | purpose |
|---|---|
| `start_fw_capture.ps1` | USBPcap on all 3 roothubs → fw_update_uN.pcap |
| `dotnet_reflect.py` | reflect FWU .NET DeviceInterface → cmd names |
| `disasm_fwudll.py` | x86 disasm FWUpdaterDLL (proves no host crypto) |
| `extract_regions.py` | pcap → per-region firmware bins (D=9) |
| `find_data_offset.py`, `verify_reconstruction.py`, `settle_layout.py`, `settle_D_final.py` | brute/verify the chunk-data offset D |
| `cryptanalyze_fw.py`, `decisive_verdict.py`, `crc_*.py` | rule out encryption / chase the (non-existent) CRC |
| `prove_plaintext.py`, `verify_plaintext.py` | bl-target self-consistency = plaintext proof |
| `phase0_sig.py` | enumerate DFU frames; prove no signature |
| `dfu_transition.py` | per-frame USB dev-addr/wIndex → 3-phase flow |
| `gen_fwupdate_blob.py`, `validate_fwupdate.py` | build/verify the verbatim DFU replay blob |
| `fw_analyze.py`, `fw_func.py`, `fw_flash.py`, `fw_find_pair.py`, `fw_trigger.py`, `fw_setflag.py` | region-03 static analysis (dispatcher, callgraph, SVCs, idle/power, persistence) |
| `make_strflip.py` | template: locate-by-content → patch → recompute pkt[88] → mod blob (string demo) |
| `make_sleep_patch.py`, `make_sleep_patch2.py` | wake-lag patch attempts (#1 timeout consts, #2 skip deep power-save) |
| `src/fwupdate.rs` | the flasher (Rust, in the daemon binary) |
| `assets/fwupdate_stock_replay.bin` | verbatim captured DFU = recovery image + modify base |

## 11. From-scratch replication recipe

1. Install USBPcap. Get a Joro on wired USB + Synapse able to FW-update.
2. `start_fw_capture.ps1`, trigger Synapse FW update, save pcap (§9.1).
3. `extract_regions.py` → region bins; `find_data_offset.py` to confirm
   D (expect 9; if a region looks like noise, suspect D off-by-one, not
   encryption — cross-check with `decisive_verdict.py`) (§9.3).
4. (Optional but recommended) unpack + RE the updater EXE (§9.2) to get
   semantic command names and confirm zero host-side crypto.
5. `phase0_sig.py` to enumerate the DFU command set & confirm
   no-signature; `dfu_transition.py` for the 3-phase device/iface map.
6. `gen_fwupdate_blob.py` + `validate_fwupdate.py`; build the daemon;
   `fw-flash-stock --probe` (zero-risk transport test) → `--commit`
   (stock round-trip) (§9.5).
7. To modify: copy the `make_strflip.py` pattern — find target bytes by
   *content* in the D=9 region bin, map region-offset → the captured
   `10:02` chunk (tag/page/off, data at `args[9]`), patch the copy of
   the stock blob, recompute only that frame's `report[88]` (XOR
   `[2..88)`), leave `args[5:9]`, write `assets/fwupdate_mod_replay.bin`,
   `cargo build`, `fw-flash-stock --commit-mod`. Always self-verify the
   patched bytes by content before writing.
8. Recovery from any bad flash: `fw-flash-stock --commit` (stock). A
   flash interrupted before a complete program auto-recovers; a clean
   bootloader handshake without completion needs a manual power-cycle.

## 12. False trails & lessons (read before re-deriving)

- **"Firmware is encrypted / needs sacrificial hardware"** — FALSE. It
  was a D=8-vs-D=9 off-by-one in our own extractor. An off-by-N mimics a
  cipher perfectly (code-like entropy, no disassembly). *Always* verify
  reconstruction against an external invariant (vector table, bl
  self-consistency) before concluding "encrypted". Entropy ~6.6 (not
  ~7.99), non-uniform, serially-correlated ⇒ NOT a strong cipher.
- **The per-chunk CRC** (`args[5:9]`) — chased for a long time; it is
  **not enforced**. Resolve such questions empirically (a recoverable
  modified flash) once the pipeline exists, rather than by endless
  black-box cryptanalysis.
- **DFU re-enumerates** (0x02CD → 0x110E → 0x02CD), each phase on a
  different USB iface — a single-handle flasher fails. Use hidapi and
  re-open per phase.
- **Dongle HyperSpeed pairing needs a Razer mouse** as a Synapse anchor;
  the dongle bond does NOT survive a reflash (the BLE bond does — it
  lives in config flash outside the DFU regions). Plan transport
  accordingly.
- **Don't guess-and-flash.** Wake-lag patches #1/#2 were partly blind;
  each costs ~20 min (wired-switch + flash + idle). Reproduce the bug
  deterministically and localize via RE *before* flashing; use the
  "decrease the timeout for a fast confirm" trick to make iterations
  ~1 min instead of ~15.
