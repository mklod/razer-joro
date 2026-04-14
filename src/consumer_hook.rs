// src/consumer_hook.rs — host-side Consumer HID interception for Joro
// Last modified: 2026-04-13--1625
//
// Joro's F-row emits Consumer Control reports in mm-primary mode (F5=Mute,
// F8=BrightnessDown, etc.). Windows handles the known ones natively, but
// we want to intercept specific usages and emit replacement keyboard VKs
// so e.g. F4 can be made to behave like a rename key.
//
// Architecture:
//   - Background thread opens Joro's Consumer Control HID interface via
//     hidapi (iface=1 usage_page=0x000C usage=0x0001).
//   - Loops reading reports with a 50 ms timeout.
//   - Report layout from discovery: [report_id=0x02, usage_lo, usage_hi, 0, 0, ...].
//     Zero means key-up.
//   - On every non-zero usage, looks up the config's consumer_remap table.
//     If matched, emits the replacement via SendInput. If unmatched, logs
//     the raw usage so users can discover codes organically.
//
// Note: Windows consumes consumer reports from the HID stack for its own
// use, but Razer Synapse proves that multiple concurrent openers can read
// the same reports. hidapi opens non-exclusively by default on Windows.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::Duration;

use hidapi::HidApi;

use crate::config::ConsumerRemapConfig;
use crate::keys;
use crate::remap::{make_key_input, send_inputs};

const JORO_VID: u16 = 0x1532;
const JORO_PID_WIRED: u16 = 0x02CD;
const CONSUMER_USAGE_PAGE: u16 = 0x000C;
const CONSUMER_USAGE: u16 = 0x0001;
// System Control usage page carries keys Joro routes outside the
// Consumer Devices pipeline (e.g. F4 "arrange windows" candidate,
// System Sleep, System Power Down, etc.).
const SYSTEM_USAGE_PAGE: u16 = 0x0001;
const SYSTEM_USAGE: u16 = 0x0080;

/// Known Joro consumer usage names (discovered 2026-04-13 via
/// proto/consumer_discover.py — see CHANGELOG for the full mapping).
/// Config entries can use these names OR a raw hex string like "0x00e2".
static CONSUMER_USAGE_TABLE: &[(&str, u16)] = &[
    // USB HID Consumer Control usage codes actually emitted by Joro
    ("Mute",           0x00E2),
    ("VolumeUp",       0x00E9),
    ("VolumeDown",     0x00EA),
    ("BrightnessUp",   0x006F),
    ("BrightnessDown", 0x0070),
    // Common media usages (likely F10..F12 — verify by logging)
    ("PlayPause",      0x00CD),
    ("NextTrack",      0x00B5),
    ("PrevTrack",      0x00B6),
    ("Stop",           0x00B7),
    // Application Control candidates for F4 "arrange windows"
    ("ACViewToggle",       0x029D),
    ("ACTaskManagement",   0x029F),
    ("ACWindowManagement", 0x02A0),
    ("ACScreenManagement", 0x02A2),
];

fn parse_consumer_usage(name_or_hex: &str) -> Option<u16> {
    let s = name_or_hex.trim();
    // Raw hex form: "0x00E2" or "0xE2"
    if let Some(rest) = s.strip_prefix("0x").or_else(|| s.strip_prefix("0X")) {
        if let Ok(v) = u16::from_str_radix(rest, 16) {
            return Some(v);
        }
    }
    // Named form: case-insensitive lookup
    let lc = s.to_lowercase();
    for (n, v) in CONSUMER_USAGE_TABLE {
        if n.to_lowercase() == lc {
            return Some(*v);
        }
    }
    None
}

/// Resolve a `to` string into a (modifier_vks, key_vk) tuple via the
/// existing keys::parse_key_combo helper. Returns None for unparseable.
fn parse_output_combo(to: &str) -> Option<(Vec<u16>, u16)> {
    keys::parse_key_combo(to)
}

/// Compiled entry: consumer usage → output key combo.
#[derive(Clone)]
struct ConsumerRemapEntry {
    usage: u16,
    modifier_vks: Vec<u16>,
    key_vk: u16,
    label: String,
}

fn compile_entries(cfg: &[ConsumerRemapConfig]) -> Vec<ConsumerRemapEntry> {
    let mut out = Vec::new();
    for entry in cfg {
        let from = entry.from.trim();
        let to = entry.to.trim();
        if from.is_empty() || to.is_empty() {
            continue;
        }
        let usage = match parse_consumer_usage(from) {
            Some(u) => u,
            None => {
                eprintln!(
                    "Warning: consumer_remap '{from}' → '{to}' — unknown source usage, skipping"
                );
                continue;
            }
        };
        let (modifier_vks, key_vk) = match parse_output_combo(to) {
            Some(p) => p,
            None => {
                eprintln!(
                    "Warning: consumer_remap '{from}' → '{to}' — output not parseable, skipping"
                );
                continue;
            }
        };
        let label = if entry.name.is_empty() {
            format!("{from} → {to}")
        } else {
            entry.name.clone()
        };
        out.push(ConsumerRemapEntry {
            usage,
            modifier_vks,
            key_vk,
            label,
        });
    }
    out
}

/// Background thread that reads consumer reports and emits remapped keys.
/// Dropping the handle sets the shutdown flag and joins the thread.
pub struct ConsumerHook {
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<()>>,
}

impl ConsumerHook {
    /// Start the hook thread for the given remap config. Returns None if
    /// no Joro consumer interface is found or the config is empty.
    pub fn start(cfg: &[ConsumerRemapConfig]) -> Option<Self> {
        let entries = compile_entries(cfg);
        if entries.is_empty() {
            return None;
        }
        let stop = Arc::new(AtomicBool::new(false));
        let stop_thread = stop.clone();
        let entries_map: HashMap<u16, ConsumerRemapEntry> =
            entries.iter().map(|e| (e.usage, e.clone())).collect();

        let thread = thread::Builder::new()
            .name("joro-consumer-hook".into())
            .spawn(move || run_loop(stop_thread, entries_map))
            .ok()?;

        Some(ConsumerHook {
            stop,
            thread: Some(thread),
        })
    }
}

impl Drop for ConsumerHook {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

/// Open every Joro HID interface we want to monitor: Consumer Control
/// (0x000C / 0x0001) and System Control (0x0001 / 0x0080). Joro routes
/// different F-row keys through different interfaces, so we listen on
/// both. Returns opened devices tagged with a label for logging.
fn open_input_interfaces(api: &HidApi) -> Vec<(String, hidapi::HidDevice)> {
    let mut out = Vec::new();
    let mut candidates: Vec<(&hidapi::DeviceInfo, &'static str)> = api
        .device_list()
        .filter_map(|d| {
            if d.vendor_id() != JORO_VID || d.product_id() != JORO_PID_WIRED {
                return None;
            }
            if d.usage_page() == CONSUMER_USAGE_PAGE && d.usage() == CONSUMER_USAGE {
                Some((d, "consumer"))
            } else if d.usage_page() == SYSTEM_USAGE_PAGE && d.usage() == SYSTEM_USAGE {
                Some((d, "system"))
            } else {
                None
            }
        })
        .collect();
    // Prefer entries with a real interface number (Windows sometimes
    // reports iface=-1 ghost entries for multi-collection devices).
    candidates.sort_by_key(|(d, _)| d.interface_number() < 0);

    let mut opened_kinds = std::collections::HashSet::new();
    for (d, kind) in &candidates {
        if opened_kinds.contains(kind) {
            continue; // already have one for this kind
        }
        match api.open_path(d.path()) {
            Ok(dev) => {
                eprintln!(
                    "joro-consumer-hook: opened {} iface={} path={:?}",
                    kind,
                    d.interface_number(),
                    d.path()
                );
                opened_kinds.insert(*kind);
                out.push((kind.to_string(), dev));
            }
            Err(e) => {
                eprintln!(
                    "joro-consumer-hook: open {} iface={} failed ({e:?}), trying next",
                    kind,
                    d.interface_number()
                );
            }
        }
    }
    out
}

fn run_loop(stop: Arc<AtomicBool>, entries: HashMap<u16, ConsumerRemapEntry>) {
    let api = match HidApi::new() {
        Ok(a) => a,
        Err(e) => {
            eprintln!("joro-consumer-hook: HidApi::new failed: {e}");
            return;
        }
    };
    let devices = open_input_interfaces(&api);
    if devices.is_empty() {
        eprintln!("joro-consumer-hook: no consumer/system HID interfaces opened");
        return;
    }
    eprintln!(
        "joro-consumer-hook: running with {} remap(s) across {} interface(s)",
        entries.len(),
        devices.len()
    );
    for e in entries.values() {
        eprintln!(
            "  usage=0x{:04x} → mods={:?} key=0x{:04x} ({})",
            e.usage, e.modifier_vks, e.key_vk, e.label
        );
    }

    let mut buf = [0u8; 64];
    // Per-interface "last emitted usage" so key-down/key-up pair correctly
    // even when both interfaces deliver reports at once.
    let mut last_usage: HashMap<String, u16> = HashMap::new();

    while !stop.load(Ordering::Relaxed) {
        for (kind, dev) in &devices {
            let n = match dev.read_timeout(&mut buf, 10) {
                Ok(n) => n,
                Err(e) => {
                    eprintln!("joro-consumer-hook: {kind} read error: {e}");
                    thread::sleep(Duration::from_millis(500));
                    continue;
                }
            };
            if n < 3 {
                continue;
            }
            // Joro report format: [report_id, usage_lo, usage_hi, ...]
            // (report_id varies by collection: 0x02 for consumer, 0x03 for system on some devices)
            let usage = u16::from_le_bytes([buf[1], buf[2]]);
            if usage == 0 {
                // Key-up. If we intercepted a key-down for this interface,
                // emit the replacement's key-up now.
                if let Some(prev) = last_usage.remove(kind) {
                    if let Some(entry) = entries.get(&prev) {
                        emit_combo_up(entry);
                    }
                }
                continue;
            }
            if let Some(entry) = entries.get(&usage) {
                eprintln!(
                    "joro-consumer-hook: {kind} usage=0x{usage:04x} intercepted → {} ({})",
                    entry.label, entry.label
                );
                last_usage.insert(kind.clone(), usage);
                emit_combo_down(entry);
            } else {
                // Unknown usage — always log so the user can discover codes organically
                let raw_hex: String = buf[..n]
                    .iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<Vec<_>>()
                    .join(" ");
                eprintln!(
                    "joro-consumer-hook: {kind} unhandled usage=0x{usage:04x} raw=[{raw_hex}]"
                );
            }
        }
    }
    eprintln!("joro-consumer-hook: stopped");
}

fn emit_combo_down(entry: &ConsumerRemapEntry) {
    let mut inputs = Vec::with_capacity(entry.modifier_vks.len() + 1);
    for &m in &entry.modifier_vks {
        inputs.push(make_key_input(m, false));
    }
    inputs.push(make_key_input(entry.key_vk, false));
    send_inputs(&inputs);
}

fn emit_combo_up(entry: &ConsumerRemapEntry) {
    let mut inputs = Vec::with_capacity(entry.modifier_vks.len() + 1);
    inputs.push(make_key_input(entry.key_vk, true));
    for &m in entry.modifier_vks.iter().rev() {
        inputs.push(make_key_input(m, true));
    }
    send_inputs(&inputs);
}
