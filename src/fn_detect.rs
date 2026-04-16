// src/fn_detect.rs — read Joro vendor HID reports to detect Fn-held state
// Last modified: 2026-04-14
//
// The Joro Fn key doesn't reach WH_KEYBOARD_LL — it's absorbed by the
// keyboard firmware before Windows sees any scan code. On both wired and
// BLE transports, however, Joro exposes the Fn state on a vendor HID
// collection (usage_page=0x0001 usage=0x0000) as a 12-byte report with
// the format:
//
//     [report_id=0x05, 0x04, state, 0, 0, 0, 0, 0, 0, 0, 0, 0]
//
// where state = 0x01 on Fn press and 0x00 on Fn release. Verified
// empirically 2026-04-14 on BLE (see `captures/fn_detect_ble.log` and
// `project_hypershift_runtime_enable_flag.md` memory).
//
// This module has two entry points:
//   - `start()`: production path. Enumerates all readable Joro HID
//     collections and spawns one reader thread per collection. Updates
//     the FN_HELD atomic. Idempotent — safe to call on every device
//     connect.
//   - `spawn_diagnostic()`: verbose discovery path. Logs every report
//     from every readable collection with timestamps. Used by the
//     `fn-detect` CLI subcommand for exploring new devices.

use std::collections::HashSet;
use std::ffi::CString;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

const RAZER_VID: u16 = 0x1532;
#[allow(dead_code)] const JORO_PID_WIRED: u16 = 0x02CD;
#[allow(dead_code)] const JORO_PID_DONGLE: u16 = 0x02CE;

/// Atomic tracking whether the Fn key is currently held. Updated by the HID
/// reader thread, read by the WH_KEYBOARD_LL hook callback.
pub static FN_HELD: AtomicBool = AtomicBool::new(false);

/// Track which HID collection paths we've already spawned a reader for.
/// `start()` is idempotent: new readers are spawned only for paths we
/// haven't seen, so calling it on each device connect handles transport
/// changes cleanly.
static OPENED_PATHS: Mutex<Option<HashSet<CString>>> = Mutex::new(None);

/// Convenience accessor for the hook callback.
#[allow(dead_code)]
pub fn fn_held() -> bool {
    FN_HELD.load(Ordering::Relaxed)
}

/// Production entry point: enumerate Joro HID collections, open every
/// non-access-denied one, and spawn a background reader thread per
/// collection that updates [`FN_HELD`] on Fn state transitions.
///
/// Idempotent — safe to call repeatedly. Tracks already-opened paths in
/// `OPENED_PATHS` so re-calls only handle newly appeared collections
/// (e.g. after a wired↔BLE transport change).
pub fn start() {
    if let Err(e) = enumerate_and_spawn() {
        eprintln!("fn-detect: start error: {e}");
    }
}

fn enumerate_and_spawn() -> Result<(), String> {
    let api = hidapi::HidApi::new().map_err(|e| format!("hidapi init: {e}"))?;

    let mut guard = OPENED_PATHS.lock().unwrap();
    let opened = guard.get_or_insert_with(HashSet::new);

    let candidates: Vec<_> = api
        .device_list()
        .filter(|d| {
            d.product_string()
                .map(|p| p.to_lowercase().contains("joro"))
                .unwrap_or(false)
                || d.vendor_id() == RAZER_VID
        })
        .cloned()
        .collect();

    if candidates.is_empty() {
        eprintln!("fn-detect: no Joro HID collections visible; will retry on next device connect");
        return Ok(());
    }

    for info in candidates {
        let path: CString = info.path().to_owned();
        if opened.contains(&path) {
            continue;
        }
        let dev = match api.open_path(&path) {
            Ok(d) => d,
            Err(e) => {
                let es = format!("{e}");
                if !es.to_lowercase().contains("denied") {
                    eprintln!(
                        "fn-detect: open failed (usage=0x{:04X}/0x{:04X}): {es}",
                        info.usage_page(),
                        info.usage()
                    );
                }
                continue;
            }
        };
        let _ = dev.set_blocking_mode(true);

        // Probe read access. Hidapi on Windows returns "Access is denied"
        // for keyboard/mouse collections because the OS HID stack owns
        // them exclusively. Skip those silently.
        let mut probe = [0u8; 64];
        if let Err(e) = dev.read_timeout(&mut probe, 50) {
            if format!("{e}").to_lowercase().contains("denied") {
                continue;
            }
        }
        // If the probe returned a real report, apply it immediately so we
        // don't miss the first Fn event right after start.
        if probe[0] == 0x05 && probe[1] == 0x04 {
            FN_HELD.store(probe[2] == 0x01, Ordering::Release);
        }

        eprintln!(
            "fn-detect: reading usage=0x{:04X}/0x{:04X}",
            info.usage_page(),
            info.usage()
        );
        opened.insert(path);

        thread::spawn(move || {
            let mut buf = [0u8; 64];
            let mut last_held = false;
            let mut last_backlight: Option<u8> = None;
            loop {
                match dev.read_timeout(&mut buf, 1000) {
                    Ok(n) if n >= 3 && buf[0] == 0x05 && buf[1] == 0x04 => {
                        let held = buf[2] == 0x01;
                        if held != last_held {
                            eprintln!("fn-detect: FN_HELD {} -> {} (report {:02x} {:02x} {:02x})",
                                last_held, held, buf[0], buf[1], buf[2]);
                            last_held = held;
                        }
                        FN_HELD.store(held, Ordering::Release);
                    }
                    // Backlight-change telemetry — fires from hardware MM F10/F11
                    // as `06 05 08 XX` where XX is the new firmware backlight
                    // level in 0..255. Forward to main so the UI slider syncs.
                    Ok(n) if n >= 4 && buf[0] == 0x06 && buf[1] == 0x05 && buf[2] == 0x08 => {
                        let level = buf[3];
                        if last_backlight != Some(level) {
                            last_backlight = Some(level);
                            crate::post_user_event(crate::UserEvent::BacklightObserved(level));
                        }
                    }
                    Ok(_) => {}
                    Err(_) => {
                        // Device disconnected, transport change, etc.
                        // Sleep briefly and retry; when the device comes
                        // back the next read will succeed. If the handle
                        // is permanently dead, read will keep erroring
                        // and we just spin — daemon restart fixes it.
                        thread::sleep(Duration::from_millis(500));
                    }
                }
            }
        });
    }
    Ok(())
}

/// Spawn a diagnostic thread that opens every Joro HID interface and prints
/// any input reports that change. Use this to identify which interface and
/// which byte carries the Fn state. Once known, the code should be simplified
/// to a single-interface reader that just updates FN_HELD.
#[allow(dead_code)]
pub fn spawn_diagnostic() {
    thread::spawn(|| {
        let api = match hidapi::HidApi::new() {
            Ok(a) => a,
            Err(e) => {
                eprintln!("fn-detect: hidapi init failed: {e}");
                return;
            }
        };

        // First pass: log EVERY HID device so we can find Joro's entry
        // regardless of whether it's connected via USB (Razer VID) or BLE
        // (Bluetooth assigned VID, path typically contains "Bluetooth").
        eprintln!("fn-detect: enumerating all HID devices...");
        let all: Vec<_> = api.device_list().cloned().collect();
        eprintln!("fn-detect: total {} HID devices", all.len());

        let mut candidates: Vec<hidapi::DeviceInfo> = Vec::new();
        for d in &all {
            let path_str = d.path().to_string_lossy();
            let product = d.product_string().unwrap_or("");
            let mfr = d.manufacturer_string().unwrap_or("");
            let is_razer = d.vendor_id() == RAZER_VID;
            let is_joro_name = product.to_lowercase().contains("joro")
                || mfr.to_lowercase().contains("razer")
                || path_str.to_lowercase().contains("joro");

            if is_razer || is_joro_name {
                eprintln!(
                    "fn-detect:   MATCH vid=0x{:04X} pid=0x{:04X} iface={} usage={:04X}/{:04X} product='{}' mfr='{}'",
                    d.vendor_id(),
                    d.product_id(),
                    d.interface_number(),
                    d.usage_page(),
                    d.usage(),
                    product,
                    mfr,
                );
                eprintln!("fn-detect:     path={}", path_str);
                candidates.push(d.clone());
            }
        }

        if candidates.is_empty() {
            eprintln!("fn-detect: no matching HID devices. Dumping first 20 for context:");
            for d in all.iter().take(20) {
                eprintln!(
                    "  vid=0x{:04X} pid=0x{:04X} product='{}' mfr='{}'",
                    d.vendor_id(),
                    d.product_id(),
                    d.product_string().unwrap_or(""),
                    d.manufacturer_string().unwrap_or(""),
                );
            }
            return;
        }

        let devices = candidates;
        eprintln!("fn-detect: opening {} candidate interface(s)...", devices.len());

        // Shared start time so all reader threads print the same millisecond
        // origin. Lets us correlate report arrivals with keypress times.
        let t0 = std::time::Instant::now();

        // Open each device and spawn a reader per interface.
        // Skip interfaces where opening fails OR where the first read fails
        // with access-denied (Windows owns the keyboard/mouse interfaces).
        for (idx, info) in devices.into_iter().enumerate() {
            let path = info.path().to_owned();
            let usage_page = info.usage_page();
            let usage = info.usage();

            let dev = match api.open_path(&path) {
                Ok(d) => d,
                Err(e) => {
                    eprintln!("fn-detect: [{}] open failed: {e}", idx);
                    continue;
                }
            };

            thread::spawn(move || {
                let _ = dev.set_blocking_mode(true);
                let mut buf = [0u8; 64];
                // Probe once to see if we even have read access.
                match dev.read_timeout(&mut buf, 100) {
                    Err(e) if format!("{e}").contains("denied") => {
                        eprintln!(
                            "fn-detect: [{}] usage=0x{:04X}/0x{:04X} SKIPPED (access denied)",
                            idx, usage_page, usage
                        );
                        return;
                    }
                    _ => {}
                }
                eprintln!(
                    "fn-detect: [{}] usage=0x{:04X}/0x{:04X} READY",
                    idx, usage_page, usage
                );
                loop {
                    match dev.read_timeout(&mut buf, 500) {
                        Ok(n) if n > 0 => {
                            let ms = t0.elapsed().as_millis();
                            let hex: String = buf[..n]
                                .iter()
                                .map(|b| format!("{:02x}", b))
                                .collect::<Vec<_>>()
                                .join(" ");
                            eprintln!(
                                "fn-detect: [{:>4}ms] [{}] ({}B): {}",
                                ms, idx, n, hex
                            );
                        }
                        Ok(_) => {} // timeout, no data — stay quiet
                        Err(e) => {
                            let es = format!("{e}");
                            if !es.contains("denied") {
                                eprintln!("fn-detect: [{}] read error: {es}", idx);
                            }
                            thread::sleep(Duration::from_secs(1));
                        }
                    }
                }
            });
        }
    });
}
