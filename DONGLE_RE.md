# Razer HyperSpeed Dongle ↔ Joro — Reverse Engineering Notes

> Last updated: 2026-05-22--0221  — **STATUS: pair PROVEN + daemon-integrated; dongle control-channel dormancy UNRESOLVED (§13)**
> Companion to `FIRMWARE_RE.md`. This doc covers the **dongle-pair / Synapse-free** track only.
> Goal: let any Joro owner buy a $5 HyperSpeed dongle and pair the keyboard **without owning a Razer mouse and without running Razer Synapse**. Razer ties the pairing UI to the mouse's tab, so the official path requires both — we want to replace that step end-to-end with one open-source CLI.

---

## 1. The official Synapse flow (verified 2026-05-21)

No standalone HyperSpeed Pairing utility was needed — pairing happens inside the main Synapse window:

1. Plug HyperSpeed dongle (PID `0x009C`) into a USB port. A compatible Razer HyperSpeed mouse (e.g. DeathAdder V2 X HyperSpeed) **must already be paired** to the dongle. Synapse refuses to show the pairing UI otherwise — Razer hides it behind the mouse's device tab.
2. Open Synapse → **DeathAdder V2 X HyperSpeed** tab (NOT the Joro tab) → **CUSTOMIZE**.
3. Scroll to bottom panel: **HYPERSPEED MULTI-DEVICE PAIRING** → click **Open Pairing Utility**.
4. Modal opens: "Hyperspeed Multi-Device Pairing Utility" with two slots (KEYBOARD / MOUSE). The keyboard slot shows either an empty PAIR target or the already-paired device with an UNPAIR hover button.
5. Joro side: switch is on the **HyperSpeed (2.4 GHz)** position (NOT BLE, NOT USB), and the keyboard is awake (hold any key if dozing). USB cable must be unplugged.
6. Click the keyboard slot. Pair completes silently — no progress UI, just a green border around the populated slot.

Once paired, the mouse can be powered off / physically removed; the keyboard remains bound to the dongle until explicitly unpaired (the bond record lives in dongle flash, not in Synapse).

**Implication for RE**: every dongle command we need is sent by Synapse over USB HID — there is no Razer-cloud round-trip and no hidden background utility. The full pair sequence is contained in our USBPcap capture.

Screenshots (project root):
- `2026-05-21 00_19_28-Razer Synapse.png` — DeathAdder Customize tab with the "Open Pairing Utility" entry point.
- `2026-05-21 00_20_15-Razer Synapse.png` — the pairing modal with both slots populated.

---

## 2. Hardware enumerated when dongle is the transport

```
HyperSpeed dongle (composite, multi-device):
  VID 0x1532  PID 0x009C
  Razer "Multi Device Hyperspeed Dongle"
  Several HID interfaces; Protocol30 control transfers go to iface 0
  (SET_REPORT report-id 0, wLength 90).
```

The dongle proxies BOTH the mouse and the keyboard onto separate HID collections. From the keyboard's point of view, the radio link replaces BLE; the wired-USB Protocol30 over the dongle is **functionally identical** to wired-USB Protocol30 directly into the Joro — same `class:cmd` set works (already verified for brightness, color, mode toggle in `project_dongle_protocol_format.md`).

The new thing the dongle adds is **dongle-management commands** (new classes `0x04` / `0x05` / `0x06` / `0x0B`) that the keyboard alone doesn't expose — these configure radio slots, query device presence, and orchestrate pairing.

---

## 3. Capture rig

`scripts/start_dongle_setup_capture.ps1` starts USBPcap on all three Windows roothubs in parallel (the dongle can land on any), writing to:

```
captures/dongle_setup_full_u1.pcap   (the actively used one — 2.78 MB, 484 frames)
captures/dongle_setup_full_u2.pcap
captures/dongle_setup_full_u3.pcap
```

`scripts/extract_dongle_setup_seq.py` produces an ordered human-readable transaction at `captures/dongle_setup_sequence.txt` — one line per Protocol30 frame, full args bytes, plus a run-length-collapsed view and first-occurrence skeleton.

`scripts/triage_dongle_setup.py` flags NEW `class:cmd` combos against the union of every `class:cmd` we've already decoded in prior captures (lighting, keymap, mode, power, device-info). NEW = pair-relevant.

---

## 4. Transaction skeleton (484 frames, all host→device)

```
[0-14]    Synapse boot handshake     class 0x00 (device info), class 0x07 (idle/power)
[15]      04:06  dsize=38            ← SLOT/POLL-RATE TABLE (see §5)
[16-18]   04:86  dsize=80            ← 80-byte dongle config blob (mostly zeros, byte0=0x01)
[19-32]   05:** family queries       ← per-slot device-present queries
[33-37]   0f:80/0f:84/02:8c          ← lighting + keymap prep
[53]      0b:03  args=00 04 00       ← PAIR-TRIGGER on slot 4
[54-59]   04:06 + 00:bf polling      ← table re-push, status readback
[60]      00:46  args=01             ← pair-success ACK / slot status
[61-62]   00:bf + 00:46 polling
[63]      00:41  args=01 02 da       ← BOND WRITE (slot=01, type=02, id=0xda)
[64-65]   00:bf + 00:86              ← post-bond verify
[66-80]   varied                     ← initial config push (Joro side)
[81+]     02:** + 0f:** + 07:**      ← already-understood keymap/lighting commit
                                        (identical to Hypershift-save flow)
```

Run-length collapsed full sequence lives in `captures/dongle_setup_sequence.txt`.

---

## 5. `04:06` slot/poll-rate table (decoded)

38-byte payload, structure: `01 03 05` header + 5 × 7-byte entries:

| slot | poll_a (µs ?) | poll_b |
|------|---------------|--------|
| 0    | 400 (`0x0190`)  | 400 |
| 1    | 800 (`0x0320`)  | 800 |
| 2    | 1600 (`0x0640`) | 1600 |
| 3    | 3200 (`0x0c80`) | 3200 |
| 4    | 6400 (`0x1900`) | 6400 |

Each entry: `u8 slot, u16-BE rate_a, u16-BE rate_b, u16 pad`. The rates double per slot — typical Razer multi-device radio bucket. Slot 4 (6400 µs / 156 Hz) being the longest poll matches the keyboard role (low-update-rate vs the 8 kHz mouse on slot 0). The pair-trigger at frame 53 (`0b:03 args=00 04 00`) targets **slot 4 = keyboard slot** — consistent.

This table is pushed twice (frames 15 and 54). It is **constant** across sessions (no device-specific data) and can be treated as a verbatim blob.

---

## 6. Decoded pair protocol (verified by 2-capture diff 2026-05-21)

Capture #2 (`dongle_pair2_u1.pcap`, 1975 frames) records UNPAIR + re-pair on the same dongle+Joro pair. Byte-level diff against capture #1 isolates the constant protocol from any device/session-specific bytes.

**Result**: `0xda` is **byte-identical** in both captures' `00:41` frames. It is **NOT a session token** — it's the Joro model-id constant in the dongle's bond schema (single byte; too small for per-unit serial; matches Razer's known practice of 1-byte model codes).

The **minimum Synapse-free sequence** is just 3 commands:

| Action | class:cmd | args | Meaning |
|--------|-----------|------|---------|
| Begin discovery (first pair only) | `0b:03` | `00 04 00` | begin pair on slot 4 (keyboard) |
| Write bond record | `00:41` | `01 02 da` | bond slot=01 device_type=02 device_id=0xda |
| Remove bond (UNPAIR) | `00:42` | `02 da` | remove bond for device_type=02 id=0xda |

Re-pair (dongle already cached): Synapse skipped `0b:03` and went straight to `00:41`. The dongle pulled `(type=02, id=0xda)` from its stored device list (the `00:84/85/86/87/93/c5` query churn before the bond write is how Synapse retrieves it). For an OSS replay tool, sending both commands always works — the discovery is a no-op when the device is already known.

Capture #1's frame 53 (`0b:03 args=00 04 00`) and frame 63 (`00:41 args=01 02 da`) — both proven constant.
Capture #2's frame 910 (`00:42 args=02 da`) — UNPAIR.
Capture #2's frame 1644 (`00:41 args=01 02 da`) — RE-PAIR bond write, byte-identical to capture #1.

Surrounding churn (`00:84/85/86/87/93/bf/c5`, `07:80/84` — status readbacks with zero args) is Synapse polling while the pair modal is open — **not required** for pair to succeed.

See `scripts/diff_dongle_pairs.py` and `scripts/dongle_pair2_windows.py` for the diff that produced this.

---

## 7. D→H responses — no longer needed for replay

Our current parsers only catch host→device SET_REPORT frames; the D→H interrupt-IN responses aren't recovered. **This no longer matters** for building the replay tool: the 2-capture diff proved `0xda` is constant, not a dongle-returned token. The OSS pair tool can issue commands open-loop and let the dongle handle the radio side. Response parsing is now optional, future work.

---

## 8. Synapse-free replay tool — PROVEN

**Built and validated 2026-05-21--0120.**

`src/bin/joro-dongle-pair.rs` — standalone Rust CLI, no daemon coupling.

```
joro-dongle-pair pair     # full 70-frame replay, paces 0b:03 → 2.5s wait → 00:41
joro-dongle-pair unpair   # single 00:42 02 da
joro-dongle-pair wipe     # double 00:42 (handles ghost bonds witnessed on dongle #2)
```

How it works:
1. Pre-flight unpair (`00:42 02 da`) to clear any stale Joro bond — safe no-op if none.
2. Streams `assets/dongle_pair_replay.bin` (70 × 90B frames = 6300B, captured verbatim from a working Synapse first-time pair) through HID `SetFeatureReport` on dongle iface 0.
3. Per-frame `send_feature_report` with 20 ms pacing; **2500 ms** wait specifically after `0b:03 discovery` so the dongle's radio finds the Joro before the bond write hits.
4. No response parsing needed — open-loop replay works (verified empirically).

Why 70 frames instead of the 3-command minimum: the 3 protocol-essential commands alone (`0b:03` + `00:41` + `00:42`) do reach the dongle, but it ignores discovery unless the full session handshake (00:81/82/86/c5, 04:06 slot/poll-rate table, 04:86 dongle config blob, 05:80/81/8a slot queries, 0f:80/84 lighting init, 02:8c keymap-prep) has been done first. Replaying all 70 satisfies that state machine without us having to RE its precise gating rules.

Joro side: nothing to do. The keyboard's role in pairing is passive — radio responds to the dongle's beacon. User just sets the switch to BLE/dongle (3 lights blink = ready) and lets the tool run.

Mouse anchor: **NOT required**. Empirically confirmed during validation — the mouse was switched off the entire time and the pair completed.

---

## 9. What this unlocks for OSS users

- Buy a HyperSpeed dongle separately (~$5 used, ~$25 new) — no mouse purchase needed.
- Run `joro-dongle-pair --dongle 1532:009C` → keyboard pairs in seconds, no Synapse.
- Combine with `joro-daemon` (already on the dongle transport thanks to the `09 31` heartbeat probe) for full keyboard control over the dongle: brightness, color, keymap, custom FW updates.
- Eliminates BLE-wake-lag entirely (dongle wakes faster than BLE).
- Frees the user from ever installing Razer Synapse.

---

## 10. Status

- [x] Capture #1 done (484 frames, dongle + mouse + Joro pair via Synapse).
- [x] NEW dongle-management classes identified (`0x04`, `0x05`, `0x06`, `0x0B`).
- [x] Pair commands pinpointed (`0b:03`, `00:41`, `00:42`).
- [x] `04:06` slot/poll-rate table decoded.
- [x] Capture #2 (unpair + re-pair) done — 1975 frames.
- [x] **Byte-level diff: `0xda` is CONSTANT.** Pair protocol fully decoded — 3 commands.
- [x] Capture #3 (different physical dongle) — `00:41 01 02 da` byte-identical across BOTH dongles. **Joro model-id is universal.**
- [x] `joro-dongle-pair` Rust CLI built: `src/bin/joro-dongle-pair.rs`. Embedded 70-frame Synapse replay blob: `assets/dongle_pair_replay.bin`.
- [x] **EMPIRICAL VALIDATION COMPLETE 2026-05-21--0120**: unpaired Joro via tool, then re-paired via tool — user confirmed typing works ("asdf working"). Zero Razer software running. Zero mouse anchor (mouse was switched off). Capture of our tool's successful pair: `captures/our_pair_replay_u1.pcap` (25.7 KB).
- [x] **CROSS-DONGLE VALIDATION 2026-05-21--0130**: same tool re-paired Joro on dongle #1 after a hot-swap from dongle #2. User confirmed typing works. The OSS tool is dongle-unit-agnostic. Capture: `captures/our_pair_dongle1_u1.pcap` (137 KB).
- [x] Minimum 3-command flow (`0b:03` + `00:41` + `00:42` alone) was insufficient — dongle needs the full Synapse pre-flight (handshake, slot/poll-rate config, slot queries, keymap-prep) before it'll process discovery. Verbatim 70-frame replay is the proven path.
- [ ] D→H interrupt-IN response parser (deferred — open-loop replay works fine).
- [ ] Mouse-pair sequence capture (separate utility's traffic was on a non-HID USB path our parser missed — future work if anyone wants OSS mouse pairing too).
- [ ] Add `daemon dongle-pair` subcommand wrapper so the daemon can self-pair on first run.

---

## 12. Daemon UI integration (2026-05-21)

`joro-dongle-pair`'s pair logic was folded into the daemon (`src/dongle_pair.rs`,
shared by both the standalone CLI and the daemon via `#[path]`). The settings
webview gained a connection-options modal (the "click for options" green link
under the transport indicator): **Pair to dongle** / **Unpair (switch to
Bluetooth)**, contextual to the current transport. Pair/unpair run on a
background thread (`UserEvent::DonglePairResult`) so the ~6 s replay never
freezes the UI; on completion the daemon drops the device and the periodic
reconnect re-probes.

Other daemon work this session:
- **Threaded reconnect** — `spawn_reconnect_probe` runs `open_any_device` off
  the main thread (the BLE WinRT scan + dongle heartbeat probe were freezing
  the webview to a white "not responding" screen). `JoroDevice: Send` added.
- **Transport monitor** — a background thread watches for a Joro hardware-switch
  flip (wired↔wireless) via wired-USB HID presence and posts
  `UserEvent::TransportChanged`.
- **MM/Fn mode toggle** — Synapse-style, locks to Multimedia keys when wired
  (firmware ignores runtime mode switch on wired — `0x05 NOT_SUPPORTED`) or
  when the config has any Win+X remap.
- **Custom color picker** — replaced `<input type=color>` (its native popup
  mis-anchored to screen-center on first open in WebView2).
- `open_any_device` skips BLE when a dongle is physically present (a
  bonded-in-Windows-but-on-dongle Joro made `BleDevice::open()` hang the
  probe thread indefinitely on a WinRT GATT call).

## 13. Dongle CONTROL channel goes dormant after idle — UNRESOLVED

After a long keyboard-inactivity pause, **all host→keyboard control commands
on the dongle die** — class 0x0F lighting and class 0x01 mode both return
status `0x04` (bridged-RF timeout); the keyboard never responds. Input
bridging (keystrokes) keeps working. Lock/Copilot remaps die concurrently
(they need the firmware in MM mode, and `set_device_mode` is also a control
command). Unpair/re-pair revives Lock/Copilot but not lighting.

USBPcap byte-diff (`scripts/diff_daemon_vs_synapse_lighting.py`) is
**conclusive**: the daemon's dongle lighting frames are *byte-identical* to
Synapse's — same SET_REPORT setup, wLength=90, interface MI_00, device addr,
Protocol30 payload + CRC. The full Synapse command set (`0f:80/84/90/10/02/
03/04`) is replicated; SET+GET-drain matches Synapse's request/response
pattern. Still fails for the daemon; Synapse controls dongle lighting fine
live. **It is solvable** — not a hard limitation.

Leading hypothesis: the control RF channel goes dormant after idle and the
daemon (deliberately passive — no solicited polling, to avoid input lag)
never keeps it warm, whereas Synapse polls constantly. Full detail +
next steps in memory `project_dongle_lighting_dead.md` (task #22).

**Workaround:** lighting works fully on wired USB and BLE, and the keyboard
firmware persists lighting state across transports — set colour/brightness
on wired/BLE and it sticks on the dongle.

## 11. References

- `FIRMWARE_RE.md` §3 (DFU 3-phase flow), §4 (Protocol30 90B frame layout, CRC = pkt[88] XOR over bytes 2..88).
- `captures/dongle_setup_full_u1.pcap` — source capture.
- `captures/dongle_setup_sequence.txt` — full ordered transaction.
- `scripts/dongle_pair_deep.py` — pair-window full-args + table decode.
- `scripts/triage_dongle_setup.py` — NEW-class detector.
- `scripts/start_dongle_setup_capture.ps1` — capture rig.
- Project memory: `project_dongle_protocol_format.md`, `project_dongle_input_flatten.md`, `project_dongle_battery_passive.md`, `project_dongle_fn_detection_solved.md`.
