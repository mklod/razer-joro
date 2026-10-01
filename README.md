# Razer Joro without Synapse

A Synapse-free daemon, tools and notes for the **Razer Joro** keyboard: custom keymaps over Bluetooth, the HyperSpeed
dongle paired without a Razer mouse, and a reverse-engineered firmware pipeline that fixed the keyboard's wake lag.
Windows only. Not affiliated with Razer.

## What's here

- **`joro-daemon`** (Rust): tray app + settings window.
  - Per-key remaps and key combos (e.g. Lock → Delete, Win+Copilot → Ctrl+F12), Fn-layer shortcuts over Bluetooth,
    media keys on the F-row by default.
  - Backlight control, battery level and a charging estimate, external monitor brightness.
  - `joro-daemon fw-flash-stock --probe | --commit | --commit-mod`: the firmware flasher (wired USB).
- **`joro-dongle-pair`**: pair / unpair / wipe a HyperSpeed dongle without Synapse or a Razer mouse
  (replays the 70 commands Synapse sends; see `DONGLE_RE.md`).
- **The research**, written up so it can be replicated:
  - `FIRMWARE_RE.md`: the firmware is plaintext (nRF52 + Nordic SoftDevice), the DFU protocol, flash layout, the
    wake-lag patch, and **§9, how to capture and decode the firmware yourself**.
  - `DONGLE_RE.md`, `BLE_RECOVERY.md`, `JORO_FUNCTION.md`, `ARCHITECTURE.md`, `razer-joro-synapse-replacement.md`.
- `scripts/`: the Python tools used along the way (blob builders, patchers, probes).

## Build

```
cargo build --release
```

## Firmware is not included

Razer's firmware is Razer's, so it isn't in this repo. The daemon builds and runs without it; only the flasher needs it.

1. Capture your own copy of the official update (`FIRMWARE_RE.md` §9.1-9.3) and build the replay blob with
   `scripts/gen_fwupdate_blob.py`.
2. Put `fwupdate_stock_replay.bin` (and, if you make one, the patched `fwupdate_mod_replay.bin`, e.g. with
   `scripts/make_sleep_patch3.py`) in `_private/assets/` - that folder is git-ignored - or set `JORO_FW_DIR` to the
   folder that holds them.

Flashing modified firmware is at your own risk. An update interrupted before programming falls back to the old
firmware, and re-flashing stock restores it; any official Razer update replaces a patched image. Details in
`FIRMWARE_RE.md` §7.

Bluetooth addresses in the scripts and notes are placeholders (`XX:XX:XX:XX:XX:XX`): use your own keyboard's.

## License

MIT for the code, scripts and write-ups in this repo: see `LICENSE`. Not covered, owned by their makers:

- Razer's firmware, software and protocols (not included; see above). Not affiliated with or endorsed by Razer.
- Windows icons extracted for the UI (`assets/sysicons/`, `assets/osk_*`, and the tray icons generated from them)
  remain Microsoft's.
- `icons8-keyboard-96.png`: keyboard icon by [Icons8](https://icons8.com).
