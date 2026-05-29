// src/fwupdate.rs — Joro stock-firmware DFU replay (3-phase, hidapi).
//
// The Joro DFU re-enumerates twice (see memory project_dfu_flow_decoded):
//   A) PID 0x02CD iface3 : frame 0  = 00:04 enter-update
//      -- device drops to bootloader --
//   B) PID 0x110E iface0 : frames 1..N-2 = 10:80/10:01/10:02/10:83/10:05
//      -- device re-enumerates back --
//   C) PID 0x02CD iface3 : last 2 = 00:87 status, 00:0b reboot
//
// hidapi (not rusb): libusb can't claim HID-bound interfaces on Windows;
// hidapi drives Set/Get_Feature and re-opens cleanly across re-enum.
//
// A DFU interrupted BEFORE a successful 10:01+program is non-destructive
// (bootloader auto-boots the intact app — verified live 2026-05-18), so
// `Probe` mode (stop at the 10:01 boundary) validates the entire
// transport with zero brick risk.

use hidapi::HidApi;
use std::time::{Duration, Instant};

const VID: u16 = 0x1532;
const PID_APP: u16 = 0x02CD; // normal wired Joro (phases A, C)
const PID_BOOT: u16 = 0x110E; // bootloader (phase B)
const PKT: usize = 90;
const SEND_DELAY_MS: u64 = 20;

#[derive(Clone, Copy, PartialEq)]
pub enum Mode {
    Dry,       // no USB I/O — walk/validate only
    Probe,     // phases A + open-bootloader + status frames; STOP before 10:01 erase
    Commit,    // full flash — STOCK image
    CommitMod, // full flash — MODIFIED image (assets/fwupdate_mod_replay.bin)
}

const STOCK: &[u8] = include_bytes!("../assets/fwupdate_stock_replay.bin");
const STOCK_MOD: &[u8] = include_bytes!("../assets/fwupdate_mod_replay.bin");

fn open(api: &HidApi, pid: u16, iface: i32) -> Option<hidapi::HidDevice> {
    // Prefer the exact interface; fall back to first VID/PID match.
    let path = api
        .device_list()
        .find(|d| d.vendor_id() == VID && d.product_id() == pid && d.interface_number() == iface)
        .or_else(|| {
            api.device_list()
                .find(|d| d.vendor_id() == VID && d.product_id() == pid)
        })
        .map(|d| d.path().to_owned())?;
    api.open_path(&path).ok()
}

fn present(pid: u16) -> bool {
    HidApi::new()
        .map(|a| {
            a.device_list()
                .any(|d| d.vendor_id() == VID && d.product_id() == pid)
        })
        .unwrap_or(false)
}

/// Poll until `pid` is present (want=true) or gone (want=false), or timeout.
fn wait_for(pid: u16, want: bool, secs: u64) -> bool {
    let t0 = Instant::now();
    while t0.elapsed() < Duration::from_secs(secs) {
        if present(pid) == want {
            std::thread::sleep(Duration::from_millis(300)); // settle
            return true;
        }
        std::thread::sleep(Duration::from_millis(150));
    }
    false
}

fn send(dev: &hidapi::HidDevice, frame: &[u8]) -> Result<(), String> {
    let mut buf = [0u8; PKT + 1]; // [0] = report id 0
    buf[1..].copy_from_slice(frame);
    dev.send_feature_report(&buf)
        .map_err(|e| format!("send_feature_report: {e}"))?;
    std::thread::sleep(Duration::from_millis(SEND_DELAY_MS));
    Ok(())
}

fn drain(dev: &hidapi::HidDevice) {
    let mut buf = [0u8; PKT + 1];
    let _ = dev.get_feature_report(&mut buf); // 10:80 status — best-effort
}

fn dump_devs(api: &HidApi, pid: u16, tag: &str) {
    for d in api
        .device_list()
        .filter(|d| d.vendor_id() == VID && d.product_id() == pid)
    {
        eprintln!(
            "  [{tag}] pid=0x{:04x} if={} usage={:#06x}/{:#04x} path={}",
            d.product_id(),
            d.interface_number(),
            d.usage_page(),
            d.usage(),
            d.path().to_string_lossy()
        );
    }
}

pub fn flash_stock(mode: Mode) -> Result<(), String> {
    let blob: &[u8] = if mode == Mode::CommitMod { STOCK_MOD } else { STOCK };
    if blob.len() % PKT != 0 {
        return Err(format!("blob not /{PKT}: {}", blob.len()));
    }
    let frames: Vec<&[u8]> = blob.chunks_exact(PKT).collect();
    let n = frames.len();
    if n < 8 {
        return Err("stock blob too short".into());
    }
    // sanity: phase boundaries
    let f0 = frames[0];
    let fa = frames[n - 2];
    let fb = frames[n - 1];
    if !(f0[6] == 0x00 && f0[7] == 0x04) {
        return Err(format!("frame0 not 00:04 (got {:02x}:{:02x})", f0[6], f0[7]));
    }
    if !(fa[6] == 0x00 && fa[7] == 0x87 && fb[6] == 0x00 && fb[7] == 0x0b) {
        return Err(format!(
            "last2 not 00:87/00:0b (got {:02x}:{:02x} {:02x}:{:02x})",
            fa[6], fa[7], fb[6], fb[7]
        ));
    }
    eprintln!(
        "fwupdate: {n} frames | A=frame0(00:04) B=1..{} C=last2(00:87,00:0b) | mode={}",
        n - 3,
        match mode {
            Mode::Dry => "DRY",
            Mode::Probe => "PROBE(stop@10:01)",
            Mode::Commit => "COMMIT(stock)",
            Mode::CommitMod => "COMMIT(MODIFIED)",
        }
    );

    if mode == Mode::Dry {
        let mut c = std::collections::BTreeMap::new();
        for f in &frames {
            *c.entry((f[6], f[7])).or_insert(0u32) += 1;
        }
        for ((cl, cm), k) in c {
            eprintln!("  {cl:02x}:{cm:02x} x{k}");
        }
        eprintln!("fwupdate: DRY ok");
        return Ok(());
    }

    let api = HidApi::new().map_err(|e| format!("HidApi: {e}"))?;
    eprintln!("fwupdate: phase A — devices for 0x{PID_APP:04x}:");
    dump_devs(&api, PID_APP, "A");
    let dev_a = open(&api, PID_APP, 3)
        .ok_or("phase A: cannot open wired Joro 0x02CD")?;
    eprintln!("fwupdate: A open ok — sending 00:04 enter-update");
    send(&dev_a, frames[0])?;
    drop(dev_a);

    eprintln!("fwupdate: waiting for bootloader 0x{PID_BOOT:04x} (re-enum)...");
    if !wait_for(PID_BOOT, true, 8) {
        return Err("bootloader 0x110E never appeared after 00:04 \
                    (device may have ignored enter-update — keyboard intact, will auto-recover)".into());
    }
    let api2 = HidApi::new().map_err(|e| format!("HidApi2: {e}"))?;
    eprintln!("fwupdate: phase B — devices for 0x{PID_BOOT:04x}:");
    dump_devs(&api2, PID_BOOT, "B");
    let dev_b = open(&api2, PID_BOOT, 0)
        .ok_or("phase B: cannot open bootloader 0x110E iface0")?;
    eprintln!("fwupdate: B open ok");

    // frames[1 .. n-2] are the bootloader download. The device REBOOTS
    // on the FIRST 10:05 commit, so we send that one then BREAK — the
    // trailing 10:05/00:87/00:0b would just error 0x3E3 (device gone).
    // Success signal = re-enumeration back to PID_APP.
    let mut committed = false;
    for i in 1..(n - 2) {
        let f = frames[i];
        let (cl, cm) = (f[6], f[7]);
        if mode == Mode::Probe && cl == 0x10 && cm == 0x01 {
            eprintln!(
                "fwupdate: PROBE reached the 10:01 erase boundary at frame {i} \
                 — transport VALIDATED. Stopping (no erase). Bootloader will \
                 auto-recover to the intact app."
            );
            return Ok(());
        }
        if cl == 0x10 && cm == 0x05 {
            // First commit: send (tolerate error — device finalizes &
            // reboots during/right after this), then stop.
            eprintln!("fwupdate: sending 10:05 COMMIT (frame {i}) — device will finalize+reboot");
            let _ = send(&dev_b, f);
            committed = true;
            break;
        }
        if cm == 0x80 {
            send(&dev_b, f)?;
            drain(&dev_b);
        } else {
            send(&dev_b, f)
                .map_err(|e| format!("B frame {i} {cl:02x}:{cm:02x}: {e}"))?;
            if cl == 0x10 && cm == 0x01 {
                eprintln!("fwupdate: 10:01 init/erase sent — settling 2s");
                std::thread::sleep(Duration::from_millis(2000));
            }
        }
        if i % 256 == 0 {
            eprintln!("fwupdate: B {i}/{}", n - 2);
        }
    }
    drop(dev_b);
    if !committed {
        return Err("reached end of B without a 10:05 commit frame — blob malformed".into());
    }
    eprintln!("fwupdate: commit sent — waiting for re-enum to 0x{PID_APP:04x} (success signal)...");
    if !wait_for(PID_APP, true, 25) {
        return Err("device did NOT re-enumerate to 0x02CD after commit \
                    — flash may have been rejected (CRC enforced?) or stuck in \
                    bootloader; keyboard recoverable via power-cycle".into());
    }
    // Best-effort phase C — the device usually already rebooted on the
    // commit, so 00:87/00:0b may not even be needed; ignore any error.
    if let Ok(api3) = HidApi::new() {
        if let Some(dev_c) = open(&api3, PID_APP, 3) {
            let _ = send(&dev_c, frames[n - 2]); // 00:87
            let _ = send(&dev_c, frames[n - 1]); // 00:0b
        }
    }
    eprintln!(
        "fwupdate: SUCCESS — committed and re-enumerated to 0x{PID_APP:04x} \
         (new firmware booted)"
    );
    Ok(())
}
