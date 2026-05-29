// src/dongle_pair.rs — Synapse-free Razer HyperSpeed dongle ↔ Joro pair/unpair
// Last modified: 2026-05-21--0200
//
// Shared between:
//   - src/main.rs (daemon UI — IPC actions `dongle_pair` / `dongle_unpair`)
//   - src/bin/joro-dongle-pair.rs (standalone OSS CLI)
//
// Decoded protocol (cf. DONGLE_RE.md §6):
//   pair    = 0b:03 args=00 04 00   (discovery on slot 4 = keyboard)
//           + 00:41 args=01 02 da   (bond write: slot=01 type=02-kbd id=0xda-Joro)
//   unpair  = 00:42 args=02 da      (remove bond for type=02 id=0xda)
//
// The 3-command minimum is INSUFFICIENT in isolation — the dongle ignores
// `0b:03` discovery unless the full Synapse pre-flight (handshake / slot
// config / slot queries / lighting / keymap-prep) has been done first. We
// satisfy that state machine by verbatim-replaying 70 Synapse-captured
// frames from `assets/dongle_pair_replay.bin` (capture-once → replay-
// forever, same pattern as `assets/fwupdate_stock_replay.bin`).
//
// Empirically validated 2026-05-21 across two physical dongles.

use hidapi::HidApi;
use std::sync::atomic::{AtomicU8, Ordering};
use std::time::Duration;

const RAZER_VID: u16 = 0x1532;
const DONGLE_PID: u16 = 0x009C;
const PACKET_SIZE: usize = 90;
const STATUS_NEW: u8 = 0x00;

static TXID: AtomicU8 = AtomicU8::new(0x1F);

/// 70 × 90-byte Synapse-captured pair sequence (cap #1 frames 0..69).
/// Built by `scripts/build_pair_replay_blob.py`.
pub const REPLAY: &[u8] = include_bytes!("../assets/dongle_pair_replay.bin");

/// Build a Razer Protocol30 90-byte HID feature-report frame.
/// CRC at byte 88 = XOR over bytes 2..88 (proven across all captures).
pub fn build_packet(class: u8, cmd: u8, dsize: u8, args: &[u8]) -> [u8; PACKET_SIZE] {
    let mut pkt = [0u8; PACKET_SIZE];
    pkt[0x00] = STATUS_NEW;
    pkt[0x01] = TXID.fetch_add(1, Ordering::Relaxed).wrapping_add(1);
    pkt[0x05] = dsize;
    pkt[0x06] = class;
    pkt[0x07] = cmd;
    let n = args.len().min(80);
    pkt[0x08..0x08 + n].copy_from_slice(&args[..n]);
    let mut crc = 0u8;
    for b in &pkt[2..88] {
        crc ^= *b;
    }
    pkt[0x58] = crc;
    pkt
}

/// Open the dongle's MI_00 vendor-HID interface (control channel).
/// Independent of the daemon's `RazerDongle::open()` so we can pair even
/// while the daemon holds a separate handle (it usually doesn't, since
/// it falls through to BLE when no bond exists — but the two paths must
/// be coexist-safe).
fn open_dongle() -> Result<hidapi::HidDevice, String> {
    let api = HidApi::new().map_err(|e| format!("hidapi init: {e}"))?;
    let info = api
        .device_list()
        .find(|d| {
            d.vendor_id() == RAZER_VID
                && d.product_id() == DONGLE_PID
                && d.interface_number() == 0
        })
        .ok_or_else(|| {
            "Razer HyperSpeed dongle (PID 0x009C iface 0) not found. \
             Plug in the dongle and try again."
                .to_string()
        })?;
    let path = info.path().to_owned();
    api.open_path(&path).map_err(|e| format!("open_path: {e}"))
}

fn send(h: &hidapi::HidDevice, pkt: &[u8; PACKET_SIZE]) -> Result<(), String> {
    let mut buf = [0u8; PACKET_SIZE + 1];
    buf[1..].copy_from_slice(pkt);
    h.send_feature_report(&buf)
        .map_err(|e| format!("send_feature_report: {e}"))?;
    std::thread::sleep(Duration::from_millis(20));
    Ok(())
}

/// Full pair flow: pre-flight unpair + 70-frame verbatim Synapse replay
/// + 2.5 s wait specifically after the `0b:03` discovery so the dongle's
/// radio finds the Joro before the bond write hits.
///
/// Joro hardware prerequisites:
///   - Switch on the BLE/dongle position (NOT Wired-USB).
///   - Keyboard awake / 3 lights blinking white (= radio is advertising).
///
/// Returns a human-readable summary on success / Err with diagnostic
/// detail on failure. ~6 s total wall-clock duration; the caller should
/// run this on a background thread (see daemon's IPC handler).
pub fn pair() -> Result<String, String> {
    let h = open_dongle()?;

    // Pre-flight: nuke any stale Joro bond (no-op if none).
    send(&h, &build_packet(0x00, 0x42, 2, &[0x02, 0xda]))?;
    std::thread::sleep(Duration::from_millis(300));

    if REPLAY.len() % PACKET_SIZE != 0 {
        return Err(format!(
            "dongle_pair_replay.bin size {} not a multiple of {PACKET_SIZE}",
            REPLAY.len()
        ));
    }
    let n_frames = REPLAY.len() / PACKET_SIZE;
    for (idx, frame) in REPLAY.chunks_exact(PACKET_SIZE).enumerate() {
        let class = frame[6];
        let cmd = frame[7];
        let mut pkt = [0u8; PACKET_SIZE];
        pkt.copy_from_slice(frame);
        let mut buf = [0u8; PACKET_SIZE + 1];
        buf[1..].copy_from_slice(&pkt);
        h.send_feature_report(&buf)
            .map_err(|e| format!("frame {idx} ({class:02x}:{cmd:02x}): {e}"))?;
        let delay = if class == 0x0b && cmd == 0x03 {
            2500 // give the radio time to find an advertising keyboard
        } else {
            20
        };
        std::thread::sleep(Duration::from_millis(delay));
    }
    Ok(format!("pair sequence sent ({n_frames} frames)"))
}

/// Single `00:42 02 da` — removes the Joro bond from the dongle's bond
/// table. Causes the Joro to re-fall-back to BLE (or HID nothing) on the
/// next link cycle. Safe to call when no bond exists (dongle no-ops).
pub fn unpair() -> Result<String, String> {
    let h = open_dongle()?;
    send(&h, &build_packet(0x00, 0x42, 2, &[0x02, 0xda]))?;
    Ok("unpair sent (00:42 02 da)".to_string())
}

/// Double-`unpair` for ghost-bond cleanup on used dongles. Witnessed
/// once on dongle #2 (2026-05-21 capture session): a prior owner had
/// paired a Joro, the bond persisted in dongle flash, and a single
/// unpair seemed to leave a half-record. Double clears it reliably.
pub fn wipe() -> Result<String, String> {
    let h = open_dongle()?;
    send(&h, &build_packet(0x00, 0x42, 2, &[0x02, 0xda]))?;
    std::thread::sleep(Duration::from_millis(200));
    send(&h, &build_packet(0x00, 0x42, 2, &[0x02, 0xda]))?;
    Ok("wipe sent (2× 00:42 02 da)".to_string())
}
