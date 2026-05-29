// src/main.rs — Joro daemon main event loop
// Last modified: 2026-04-16

// Release builds run as a Windows GUI subsystem app — no console window.
// Debug builds (`cargo run`) keep the console so eprintln! output is visible.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod ble;
mod brightness;
mod config;
mod consumer_hook;
mod device;
mod dongle_pair;
mod fn_detect;
mod fn_detect_rawinput;
mod fwupdate;
mod keys;
mod logfile;
mod remap;
mod rzcontrol;
mod settings_window;
mod tray;
mod usb;
mod usb_dongle;
mod window_state;

use device::JoroDevice;

/// A tray submenu click — color/brightness/effect preset.
enum Preset {
    Color(&'static str),
    Brightness(u8),
    Effect(&'static str),
}

/// Flat static tray menu items (everything not in a preset submenu).
enum StaticMenu {
    Settings,
    Reload,
    OpenConfig,
    ToggleAutostart,
    Quit,
}

/// Parse a key/combo string into a (HID_modifier_byte, HID_usage_code) pair
/// suitable for the Fn-layer remap output bytes.
///
/// Examples:
///   "Home"        → (0x00, 0x4A)
///   "Ctrl+F12"    → (0x01, 0x45)
///   "Shift+End"   → (0x02, 0x4D)
///   "Win+Tab"     → (0x08, 0x2B)
///
/// HID modifier bits: 0x01=LCtrl, 0x02=LShift, 0x04=LAlt, 0x08=LGui (Win),
/// 0x10=RCtrl, 0x20=RShift, 0x40=RAlt, 0x80=RGui.
fn parse_hid_combo(s: &str) -> Option<(u8, u8)> {
    let parts: Vec<&str> = s.split('+').map(|p| p.trim()).collect();
    if parts.is_empty() {
        return None;
    }
    let key_token = parts[parts.len() - 1];
    let mut modifier: u8 = 0;
    for part in &parts[..parts.len() - 1] {
        let bit = match part.to_lowercase().as_str() {
            "ctrl" | "lctrl" | "control" => 0x01,
            "shift" | "lshift" => 0x02,
            "alt" | "lalt" => 0x04,
            "win" | "lwin" | "lgui" | "cmd" | "meta" => 0x08,
            "rctrl" => 0x10,
            "rshift" => 0x20,
            "ralt" | "altgr" => 0x40,
            "rwin" | "rgui" => 0x80,
            _ => return None,
        };
        modifier |= bit;
    }
    let usage = keys::key_name_to_hid(key_token)?;
    Some((modifier, usage))
}

/// Custom events posted to the winit event loop from cross-thread sources
/// (e.g. the webview's IPC handler, which runs on a WebView2 callback thread,
/// or the Ctrl+C handler, which runs on a Windows SetConsoleCtrlHandler thread).
#[derive(Debug, Clone)]
pub enum UserEvent {
    /// Raw JSON string from the settings webview's `window.ipc.postMessage(...)`.
    SettingsIpc(String),
    /// Ctrl+C pressed in the terminal. Triggers a graceful shutdown so Drop
    /// runs on BleDevice (releasing the WinRT connection to the keyboard).
    CtrlC,
    /// Keyboard backlight command posted from the remap LL hook thread so
    /// we can dispatch BLE I/O on the main thread (BleDevice isn't Send).
    /// Value is an absolute 0-255 brightness level.
    BacklightSet(u8),
    /// Keyboard backlight changed at the hardware level (user pressed the
    /// Joro's native F10/F11 MM keys while firmware is in MM mode). The
    /// daemon doesn't initiate this; it's reported via col05 HID telemetry
    /// `06 05 08 XX`. We update config + push state to the webview but
    /// do NOT write back to the device (that would fight the keyboard).
    BacklightObserved(u8),
    /// Battery level observed PASSIVELY from the dongle's periodic heartbeat
    /// HID report `09 31 <raw> ...` on MI01.Col08 (raw is 0-255, scale to
    /// 0-100). This is a push report the keyboard sends while awake — no
    /// solicited Protocol30 query, so zero USB control traffic and zero
    /// input lag (the reason the old battery polling was ripped out).
    /// Value is already scaled to 0-100%.
    BatteryObserved(u8),
    /// Synapse-free dongle pair attempt finished. Posted from the
    /// background thread spawned by the `dongle_pair` IPC action so the
    /// UI thread can push a result message to the webview AND drop the
    /// current device handle so `try_connect` re-probes (heartbeat probe
    /// will now see the new bond and select the dongle transport).
    DonglePairResult { ok: bool, message: String },
    /// Counterpart for `dongle_unpair` — once the bond is removed the
    /// keyboard falls back to BLE, so we drop the dongle handle and
    /// re-probe.
    DongleUnpairResult { ok: bool, message: String },
    /// A background reconnect probe (spawned by the `reconnect_device` IPC
    /// action) finished. The opened device — if any — is in the App's
    /// `pending_device` slot; the handler moves it onto `self.device` and
    /// runs `finish_connect`. Carrying the device through a shared slot
    /// rather than the event payload keeps `UserEvent` `Clone`.
    ReconnectComplete,
    /// The background transport-monitor thread detected a Joro hardware-
    /// switch flip (wired↔wireless) — the daemon's attached transport no
    /// longer matches what's physically present. Handler drops the stale
    /// device and kicks a fresh reconnect probe.
    TransportChanged,
}

use std::time::{Duration, Instant};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop, EventLoopProxy};
use winit::window::WindowId;

/// Cross-thread handle back into the main winit event loop. Populated in
/// `fn main()` before the event loop starts; read by remap.rs from the LL
/// hook thread to post backlight commands (BLE I/O must run on the main
/// thread because BleDevice isn't Send).
static GLOBAL_PROXY: std::sync::OnceLock<EventLoopProxy<UserEvent>> = std::sync::OnceLock::new();

pub fn post_user_event(event: UserEvent) {
    if let Some(p) = GLOBAL_PROXY.get() {
        let _ = p.send_event(event);
    }
}

// ── App state ─────────────────────────────────────────────────────────────────

struct App {
    tray: Option<tray::JoroTray>,
    device: Option<Box<dyn JoroDevice>>,
    config: config::Config,
    config_path: std::path::PathBuf,
    config_modified: Option<std::time::SystemTime>,
    last_device_poll: Instant,
    last_config_poll: Instant,
    last_reconnect_attempt: Option<Instant>,
    last_fn_detect_check: Option<Instant>,
    cached_battery: Option<u8>,
    _window: Option<winit::window::Window>, // hidden window to keep event loop alive
    proxy: EventLoopProxy<UserEvent>,
    settings: Option<settings_window::SettingsWindow>,
    consumer_hook: Option<consumer_hook::ConsumerHook>,
    /// Razer filter driver session for BLE Fn-primary mode. Held open
    /// for the lifetime of the feature — scancode hooks tear down on
    /// CloseHandle. See src/rzcontrol.rs.
    rzcontrol: Option<rzcontrol::RzControl>,
    /// Observer-mode rzcontrol session for DONGLE transport. Opened with
    /// `enable_hypershift_notify(true)` only — does NOT call EnableInputHook,
    /// so keys aren't blocked. Reader thread logs every event so we can
    /// discover the Fn-state event format. Drop closes handle which disables
    /// notifications.
    rzcontrol_observer: Option<rzcontrol::RzControl>,
    /// Tracks whether we've already run the one-shot Synapse-bootstrap
    /// dance this process lifetime. Bootstrap piggybacks on Synapse's
    /// filter-driver init and then kills it — we only need to do it
    /// once per daemon run.
    rzcontrol_bootstrap_done: bool,
    /// Last-applied Joro firmware device mode, cached from try_connect so the
    /// webview can render F-row labels/remap-from defaults based on which
    /// usages the keyboard is currently emitting. None = unknown/no device.
    firmware_fn_primary: Option<bool>,
    /// Last lighting state (`mode`, `color`, `brightness`) we successfully
    /// wrote to the keyboard. When the next `apply_config` call would send
    /// the same values, we skip the writes entirely — avoids the reconnect
    /// storm seen at login when an initial `set_static_color` races the
    /// not-quite-ready GATT session and fails with E_FAIL (0x80004004),
    /// which in turn gets counted as a transient disconnect and tears
    /// down the session. Cleared on daemon restart; persistence isn't
    /// needed because the keyboard firmware already holds lighting
    /// state across reboots.
    last_applied_lighting: Option<(String, String, u8)>,
    /// When set, `try_connect` skips the dongle heartbeat probe and goes
    /// straight to wired-USB / BLE. Set after an explicit user unpair so
    /// the daemon doesn't immediately re-select the dongle on the lingering
    /// (now-stale) radio heartbeats that keep arriving for several seconds
    /// before the keyboard realises its bond was wiped and gives up the link.
    /// Cleared once the deadline passes.
    skip_dongle_until: Option<Instant>,
    /// True while a background reconnect probe thread is running (spawned by
    /// the `reconnect_device` IPC). Guards against spawning a second probe
    /// while one is in flight.
    reconnecting: std::sync::Arc<std::sync::atomic::AtomicBool>,
    /// Hand-off slot: the background reconnect thread stashes the opened
    /// device here, then posts `UserEvent::ReconnectComplete`; the main
    /// thread takes it out and runs `finish_connect`.
    pending_device: std::sync::Arc<std::sync::Mutex<Option<Box<dyn JoroDevice>>>>,
    /// Current transport, shared with the background transport-monitor
    /// thread: 0 = none/disconnected, 1 = dongle, 2 = wired USB, 3 = BLE.
    /// The monitor watches for a Joro hardware-switch flip (wired↔wireless)
    /// and posts `UserEvent::TransportChanged` when the daemon's attached
    /// transport no longer matches reality.
    transport_state: std::sync::Arc<std::sync::atomic::AtomicU8>,
}

/// transport_state codes shared with the monitor thread.
mod transport_code {
    pub const NONE: u8 = 0;
    pub const DONGLE: u8 = 1;
    pub const USB: u8 = 2;
    pub const BLE: u8 = 3;
}

/// Cheap-ish HID-enumeration probe: is the wired Joro (PID 0x02CD iface 0)
/// currently enumerated? Called only from the background monitor thread —
/// `HidApi::new()` can stall for a second or two during a USB hot-plug
/// event, which is fine off the main thread but would freeze the webview
/// on it.
fn wired_joro_present() -> bool {
    match hidapi::HidApi::new() {
        Ok(api) => api.device_list().any(|d| {
            d.vendor_id() == 0x1532
                && d.product_id() == 0x02CD
                && d.interface_number() == 0
        }),
        Err(_) => false,
    }
}

/// Probe transports and open the first available, in priority order:
/// dongle (PID 0x009C, gated on a passive heartbeat probe) → wired USB
/// (PID 0x02CD) → BLE. Pure I/O, no `&App` — safe to call from a
/// background thread (that's the whole point: the dongle heartbeat probe
/// is ~2 s and `BleDevice::open()` can be 5 s+, and running either on the
/// main event-loop thread freezes the webview).
///
/// `skip_dongle`: bypass the dongle probe (set during the post-unpair
/// window — see `App::resolve_skip_dongle`).
/// `include_ble`: when false, the BLE branch is skipped (periodic
/// reconnect ticks avoid the slow WinRT scan; BLE is only probed on
/// startup and on a user-initiated Reconnect).
fn open_any_device(skip_dongle: bool, include_ble: bool) -> Option<Box<dyn JoroDevice>> {
    // Is a HyperSpeed dongle physically plugged in (enumerated)? This is a
    // pure device-list walk — cheap, no I/O.
    let dongle_hw_present = match hidapi::HidApi::new() {
        Ok(api) => api
            .device_list()
            .any(|d| d.vendor_id() == 0x1532 && d.product_id() == 0x009C),
        Err(_) => false,
    };

    // Prefer the dongle ONLY if it's actively bridging the keyboard (heartbeat
    // present). The dongle may be plugged but unbonded / the keyboard may be
    // on BLE — in that case skipping BLE leaves the daemon disconnected even
    // though the keyboard is reachable. The earlier "dongle present → never
    // try BLE" guard was over-aggressive (covered the niche "keyboard bonded
    // in Windows BT but actually on dongle → BleDevice::open() hangs" case)
    // and locked out the much commoner "dongle plugged + Joro on BLE" case.
    if !skip_dongle && dongle_hw_present && usb_dongle::RazerDongle::dongle_bridging_keyboard() {
        if let Some(d) = usb_dongle::RazerDongle::open() {
            return Some(Box::new(d));
        }
    }
    if let Some(d) = usb::RazerDevice::open() {
        return Some(Box::new(d));
    }
    if include_ble {
        if let Some(d) = ble::BleDevice::open() {
            return Some(Box::new(d));
        }
    } else {
        eprintln!(
            "joro-daemon: open_any_device (no-BLE) — no dongle/USB found; BLE skipped"
        );
    }
    None
}

impl App {
    fn new(proxy: EventLoopProxy<UserEvent>) -> Self {
        let config_path = config::ensure_config()
            .unwrap_or_else(|e| {
                eprintln!("Warning: could not ensure config: {e}");
                config::config_path()
            });

        let cfg = config::Config::load(&config_path)
            .unwrap_or_else(|e| {
                eprintln!("Warning: could not load config: {e}");
                // Return a minimal default config
                toml::from_str(
                    "[lighting]\nmode = \"static\"\ncolor = \"#FFFFFF\"\nbrightness = 128\n",
                )
                .expect("Failed to parse hardcoded default config")
            });

        let config_modified = std::fs::metadata(&config_path)
            .ok()
            .and_then(|m| m.modified().ok());

        let now = Instant::now();
        App {
            tray: None,
            device: None,
            config: cfg,
            config_path,
            config_modified,
            last_device_poll: now,
            last_config_poll: now,
            last_reconnect_attempt: None,
            last_fn_detect_check: None,
            cached_battery: None,
            _window: None,
            proxy,
            settings: None,
            consumer_hook: None,
            rzcontrol: None,
            rzcontrol_observer: None,
            rzcontrol_bootstrap_done: false,
            firmware_fn_primary: None,
            last_applied_lighting: None,
            skip_dongle_until: None,
            reconnecting: std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false)),
            pending_device: std::sync::Arc::new(std::sync::Mutex::new(None)),
            transport_state: std::sync::Arc::new(
                std::sync::atomic::AtomicU8::new(transport_code::NONE),
            ),
        }
    }

    /// Spawn the background transport-monitor thread. It polls (off the main
    /// thread) for a Joro hardware-switch flip — wired↔wireless — and posts
    /// `UserEvent::TransportChanged` when the daemon's attached transport no
    /// longer matches what's physically present. The reliable signal is "is
    /// the wired Joro (PID 0x02CD) enumerated": flipping the switch to wired
    /// makes it appear, flipping away makes it disappear. (dongle-vs-BLE is
    /// the *same* hardware switch position — the firmware picks between them
    /// — so we only need to watch the wired boundary.)
    fn spawn_transport_monitor(&self) {
        use std::sync::atomic::Ordering;
        let state = self.transport_state.clone();
        std::thread::spawn(move || loop {
            std::thread::sleep(Duration::from_secs(3));
            let cur = state.load(Ordering::Acquire);
            let wired = wired_joro_present();
            let mismatch = match cur {
                transport_code::USB => !wired, // on wired but cable/switch gone
                transport_code::DONGLE | transport_code::BLE => wired, // wireless but wired appeared
                _ => false, // disconnected — the periodic reconnect handles it
            };
            if mismatch {
                eprintln!(
                    "joro-daemon: transport-monitor — switch flip detected \
                     (state={cur} wired_present={wired})"
                );
                post_user_event(UserEvent::TransportChanged);
            }
        });
    }

    /// Sync the Razer filter driver state to match config. Called after
    /// connect, disconnect, config reload, and during periodic poll.
    /// Idempotent.
    ///
    /// Intentionally NOT gated on the daemon's BLE attach state — the
    /// filter driver is a PnP-level component that exists as long as
    /// Joro is paired in Windows, independent of our own GATT session.
    /// Our BLE attach frequently fails transiently but rzcontrol is
    /// still usable.
    fn sync_rzcontrol(&mut self) {
        // Auto-open-on-DONGLE attempted 2026-04-24 to capture Fn events from
        // the kernel filter driver (Synapse uses this path through dongle for
        // Hypershift). Reverted because the filter's EnableInputHook
        // intercepts ALL keys and our reader only re-injects F5..F12 — every
        // other key (arrows, Lock, Copilot, etc.) was getting silently
        // dropped. Need a different read mode (notify-only? or full
        // pass-through reinject?) before re-enabling. For now, leave gated on
        // ble_fn_primary as before.
        let want = self.config.ble_fn_primary;

        if want && self.rzcontrol.is_none() {
            // One-shot Synapse-bootstrap: launches RazerAppEngine for ~6s
            // to prime the filter driver's internal state, then kills it
            // so we own the filter. Only runs once per daemon lifetime.
            if !self.rzcontrol_bootstrap_done {
                match rzcontrol::bootstrap_filter_driver(6) {
                    Ok(true) => eprintln!("joro-daemon: rzcontrol filter driver bootstrapped"),
                    Ok(false) => {
                        // User has Synapse running — respect it and don't race.
                        eprintln!(
                            "joro-daemon: Synapse is running; skipping rzcontrol (would race with Synapse)"
                        );
                        self.rzcontrol_bootstrap_done = true;
                        return;
                    }
                    Err(e) => eprintln!("joro-daemon: rzcontrol bootstrap failed: {e}"),
                }
                self.rzcontrol_bootstrap_done = true;
            }
            match rzcontrol::RzControl::open() {
                Ok(mut rz) => match rz.hook_all(rzcontrol::FN_PRIMARY_SCANCODES) {
                    Ok(()) => {
                        eprintln!(
                            "joro-daemon: rzcontrol Fn-primary hooks installed ({} keys)",
                            rzcontrol::FN_PRIMARY_SCANCODES.len()
                        );
                        self.rzcontrol = Some(rz);
                    }
                    Err(e) => eprintln!("joro-daemon: rzcontrol hook_all failed: {e}"),
                },
                Err(e) => eprintln!("joro-daemon: rzcontrol open failed: {e}"),
            }
        } else if !want && self.rzcontrol.is_some() {
            // Drop closes handle → driver tears down rules.
            self.rzcontrol = None;
            eprintln!("joro-daemon: rzcontrol Fn-primary hooks released");
        }
    }

    /// Try to open the device; on success, apply config and update tray.
    /// Probe order: dongle (PID 0x009C) → direct USB (PID 0x02CD) → BLE.
    ///
    /// `include_ble`: when false, the BLE branch is skipped entirely. Set
    /// false for periodic reconnect ticks — `BleDevice::open()` does a
    /// WinRT BLE scan that can block the main event loop for 5+ seconds,
    /// freezing the webview (white-screen "not responding" reported
    /// 2026-05-21 on wired↔dongle hot-swap). Set true for user-initiated
    /// reconnect (the user's clicking expects a brief wait) and initial
    /// startup. Once the BLE bond is open the daemon stays attached
    /// indefinitely; this only affects the (re-)open path.
    fn try_connect(&mut self) {
        self.try_connect_inner(true);
    }

    fn try_connect_inner(&mut self, include_ble: bool) {
        if self.device.is_some() {
            return;
        }
        let skip_dongle = self.resolve_skip_dongle();
        let Some(dev) = open_any_device(skip_dongle, include_ble) else {
            return;
        };
        self.finish_connect(dev);
    }

    /// Spawn a background thread that probes all transports (dongle → wired
    /// → BLE) and, if one is found, stashes the opened device in
    /// `pending_device` and posts `UserEvent::ReconnectComplete`. The main
    /// thread picks it up and runs `finish_connect`.
    ///
    /// This is THE reconnect path — used by both the periodic `check_device`
    /// poll and the user's Reconnect action. Running the probe off the main
    /// thread is essential: the dongle heartbeat probe (~2 s) and the BLE
    /// WinRT scan (5 s+) would otherwise freeze the webview. No-op if a
    /// device is already attached or a probe is already in flight.
    fn spawn_reconnect_probe(&mut self) {
        use std::sync::atomic::Ordering;
        if self.device.is_some() || self.reconnecting.load(Ordering::Acquire) {
            return;
        }
        let skip_dongle = self.resolve_skip_dongle();
        self.reconnecting.store(true, Ordering::Release);
        let slot = self.pending_device.clone();
        let flag = self.reconnecting.clone();
        std::thread::spawn(move || {
            // include_ble = true — the BLE WinRT scan is safe here because
            // we're off the main event-loop thread.
            let dev = open_any_device(skip_dongle, true);
            *slot.lock().unwrap() = dev;
            flag.store(false, Ordering::Release);
            post_user_event(UserEvent::ReconnectComplete);
        });
    }

    /// Resolve (and consume) the "skip dongle" window set by a user-triggered
    /// unpair — keeps the daemon off a freshly-unbonded dongle's lingering
    /// stale heartbeats. See `skip_dongle_until`.
    fn resolve_skip_dongle(&mut self) -> bool {
        match self.skip_dongle_until {
            Some(t) if Instant::now() < t => {
                eprintln!(
                    "joro-daemon: try_connect skipping dongle (user unpair window — {:?} left)",
                    t.saturating_duration_since(Instant::now())
                );
                true
            }
            Some(_) => {
                self.skip_dongle_until = None;
                false
            }
            None => false,
        }
    }

    /// Post-open: configure the freshly-attached device and wire up hooks.
    /// Runs on the MAIN thread (touches self.config / tray / settings /
    /// spawns hooks). The slow OPEN probe is done separately — either inline
    /// via `open_any_device` (startup / periodic) or on a background thread
    /// (UI Reconnect — see the `reconnect_device` IPC handler).
    fn finish_connect(&mut self, mut dev: Box<dyn JoroDevice>) {
        eprintln!("joro-daemon: {} device connected", dev.transport_name());
        // Apply user-requested firmware mode. Defaults to "auto": if any
        // trigger remap in the config targets a Win-modified key (Win+L,
        // Win+Copilot, etc.), we keep MM mode because those combos are
        // generated by the keyboard firmware only in MM mode — Fn/driver
        // mode suppresses them and Lock/Copilot stop working. Otherwise
        // we prefer Fn mode so F4-F12 emit plain VK_F4..VK_F12 scancodes
        // the LL hook can swallow and rewrite.
        //
        // User can override via `device_mode = "fn" | "mm" | "auto"` in
        // config.toml. See memory/project_fnmm_toggle_solved.md.
        // Honor the user's saved preference. The UI shows a warning when
        // Win+X remaps exist (Lock/Copilot won't work in Fn) but doesn't
        // force the choice — same here. Default to MM for unset/legacy
        // ("", "auto") since it's the safer default that keeps Lock/Copilot
        // working.
        let want_fn = Some(self.config.device_mode == "fn");
        if let Some(fn_primary) = want_fn {
            match dev.set_device_mode(fn_primary) {
                Ok(()) => {
                    eprintln!(
                        "joro-daemon: firmware mode = {}",
                        if fn_primary { "Fn-primary" } else { "MM-primary" }
                    );
                    self.firmware_fn_primary = Some(fn_primary);
                }
                Err(e) => eprintln!("joro-daemon: set_device_mode failed: {e}"),
            }
        }

        // Keep-alive: tell firmware to disable idle sleep entirely.
        // Razer Protocol30 cmd=0x07 cmd=0x03 with seconds=0 = never sleep
        // (per openrazer convention). Try this on every connect — backend
        // default is no-op, so backends that haven't implemented it skip
        // silently. Best-effort: log on failure but don't fail the connect.
        match dev.set_idle_time(0) {
            Ok(()) => eprintln!("joro-daemon: set_idle_time(0) ok — firmware sleep disabled"),
            Err(e) => eprintln!("joro-daemon: set_idle_time(0) failed: {e}"),
        }
        // Reset + re-enumerate fn_detect HID readers. After a BLE
        // disconnect/reconnect cycle, old HID collection handles go
        // stale (Windows creates new device paths for the reconnected
        // keyboard). Without reset(), start() skips already-opened
        // paths and the reader threads spin on dead handles — Fn
        // detection silently stops working.
        fn_detect::reset();
        fn_detect::start();
        // Grace period between GATT-ready and the first config write.
        // Observed post-login: WinRT reports "connected and GATT ready"
        // while the BLE link isn't quite ready for writes yet —
        // set_static_color fails with 0x80004004 (E_FAIL), and Windows
        // flags the session as transient-disconnected, triggering a
        // reconnect storm. A short sleep lets the link settle before
        // we start pushing lighting/brightness state.
        std::thread::sleep(Duration::from_millis(500));
        self.apply_config(&mut *dev);
        let fw = dev.get_firmware().ok();
        let transport = dev.transport_name();
        eprintln!("joro-daemon: {} firmware={:?}", transport, fw);
        // Publish the transport to the monitor thread so it can detect a
        // later hardware-switch flip.
        self.transport_state.store(
            match transport {
                "DONGLE" => transport_code::DONGLE,
                "USB" => transport_code::USB,
                "BLE" => transport_code::BLE,
                _ => transport_code::NONE,
            },
            std::sync::atomic::Ordering::Release,
        );
        // One solicited battery read at connect for an immediate value on
        // BLE/USB (reliable there). Through the dongle this usually returns
        // the Ok(0) timeout sentinel → None; the passive heartbeat
        // (UserEvent::BatteryObserved) fills it in within a few seconds. No
        // repeated solicited polling — that's what caused the input lag.
        let battery = match dev.get_battery_percent() {
            Ok(0) => None,
            Ok(pct) => Some(pct),
            Err(_) => None,
        };
        eprintln!("joro-daemon: {} battery={:?}%", transport, battery);

        // Apply Fn-layer remaps from config (USB-only — class 0x02 isn't
        // available over BLE). These persist in keyboard firmware so they
        // survive reboots and are active on any transport afterward.
        // Idempotent: re-applying is safe.
        // DONGLE intentionally excluded: 2026-04-24 attempts to write firmware
        // Hypershift entries through the dongle (class=0x02 cmd=0xa4 unlock +
        // cmd=0x0d writes) regressed Copilot/Win+L/Fn behavior — likely
        // because the captured Synapse packet sequence is incomplete (we
        // reproduced cmd=0xa4 + cmd=0x0d but Synapse may also do a commit
        // command we missed, or the cmd=0xa4 we sent isn't the right
        // begin-edit-session call). Revisit after fresh Frida capture.
        if transport == "USB" {
            Self::apply_fn_remaps(&self.config, &mut *dev);
        }

        // DONGLE Fn-detection observer attempted 2026-04-24 with
        // `enable_hypershift_notify(true)` (IOCTL 0x8888301c) — turned out
        // NOT to be passive subscribe but full Hypershift-edit takeover that
        // blocks the entire keyboard. Required dongle replug to recover.
        // Don't re-enable until we understand 0x8888301c's actual semantics.
        // See memory/project_dongle_fn_detection_unsolved.md.
        if let Some(ref mut tray) = self.tray {
            tray.set_connected(true, fw.as_deref(), Some(transport));
            tray.sync_check_state(
                &self.config.lighting.color,
                self.config.lighting.brightness,
                &self.config.lighting.mode,
            );
        }
        self.device = Some(dev);
        self.cached_battery = battery;
        // Clear the reconnect backoff so a future disconnect retries quickly
        self.last_reconnect_attempt = None;
        // Start (or restart) the Consumer HID interception thread.
        // hidapi opens a separate handle (non-exclusive) so it coexists
        // with the rusb control-transfer handle we use for Protocol30
        // commands. We *always* recreate on connect rather than only
        // when None: HID interface paths can go stale across BLE
        // sleep/wake cycles (Windows reports "device is not connected"
        // on previously-opened handles) and the reader threads have no
        // self-heal — they spin on read errors forever. Same fix pattern
        // we use for fn_detect (reset + start on every reconnect).
        // Dropping the old hook joins its thread cleanly via its
        // shutdown flag.
        self.consumer_hook = None;
        self.consumer_hook = consumer_hook::ConsumerHook::start(&self.config.consumer_remap);
        // BLE Fn-primary filter: only applies when device is on BLE and
        // config enables it. sync_rzcontrol handles both cases.
        self.sync_rzcontrol();
        // If the settings window is open, push a full state update so the
        // transport indicator and battery reflect the new connection.
        if self.settings.is_some() {
            self.push_settings_state();
        }
    }

    /// Add or update a single Fn-layer HOST-side remap and save config.
    /// Unlike `update_fn_remap`, this doesn't touch firmware — the new
    /// binding is applied live by the WH_KEYBOARD_LL hook via the
    /// `FN_HOST_REMAP_TABLE` (rebuilt and swapped in atomically).
    /// Replaces any existing entry whose `from` matches case-insensitively.
    fn update_fn_host_remap(&mut self, from: String, to: String) {
        self.config
            .fn_host_remap
            .retain(|r| !r.from.eq_ignore_ascii_case(&from));
        let name = format!("Fn+{from} to {to} (host-side)");
        self.config.fn_host_remap.push(config::FnRemapConfig {
            name,
            from: from.clone(),
            to: to.clone(),
        });
        if let Err(e) = config::save_config(&self.config_path, &self.config) {
            eprintln!("Warning: save_config failed: {e}");
        }
        self.config_modified = std::fs::metadata(&self.config_path)
            .ok()
            .and_then(|m| m.modified().ok());
        // Rebuild the host-side Fn table and swap it in — the hook picks
        // up the new table on the next key event.
        let table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
        remap::update_fn_host_remap_table(table);
        eprintln!("joro-daemon: host fn-layer {from} -> {to} (applied live)");
    }

    /// Add or update a single Fn-layer remap, save config, and apply it to
    /// the device immediately if connected via USB. Replaces any existing
    /// entry whose `from` matches case-insensitively.
    fn update_fn_remap(&mut self, from: String, to: String) {
        // Remove any existing entry for the same source key
        self.config
            .fn_remap
            .retain(|r| !r.from.eq_ignore_ascii_case(&from));
        // Add the new entry
        let name = format!("Fn+{from} to {to}");
        self.config.fn_remap.push(config::FnRemapConfig {
            name,
            from: from.clone(),
            to: to.clone(),
        });
        // Save the whole config (loses comments — acceptable for UI writes)
        if let Err(e) = config::save_config(&self.config_path, &self.config) {
            eprintln!("Warning: save_config failed: {e}");
        }
        self.config_modified = std::fs::metadata(&self.config_path)
            .ok()
            .and_then(|m| m.modified().ok());
        // Apply immediately if we're on USB
        if let Some(ref mut dev) = self.device {
            if dev.transport_name() == "USB" {
                if let (Some(src), Some((modifier, usage))) = (
                    keys::key_name_to_matrix(&from),
                    parse_hid_combo(&to),
                ) {
                    match dev.set_layer_remap(src, modifier, usage) {
                        Ok(()) => eprintln!(
                            "joro-daemon: live fn-layer {from} → {to} (matrix=0x{src:02x})"
                        ),
                        Err(e) => eprintln!("Warning: live fn-layer apply failed: {e}"),
                    }
                } else {
                    eprintln!(
                        "Warning: cannot apply live fn-layer {from} → {to} (unknown matrix or output)"
                    );
                }
            } else {
                eprintln!(
                    "Note: fn-layer {from} → {to} saved to config but not applied (BLE — switch to USB and restart daemon to write to firmware)"
                );
            }
        }
    }

    /// Apply firmware Hypershift (Fn-layer) keymap remaps from config.
    /// Each `[[fn_remap]]` entry programs one source key → output key
    /// on the Hypershift layer via `set_layer_remap()` (class=0x02
    /// cmd=0x0d). Both wired and BLE transports read from the same
    /// firmware storage slot, so one USB write programs both.
    ///
    /// Commit semantics: firmware only refreshes the live Hypershift
    /// table after a transport mode switch. Writes land in storage
    /// immediately but require wired↔BLE cycling to go live. See
    /// `src/usb.rs::set_layer_remap` doc and memory
    /// `project_hypershift_commit_trigger.md` for full history.
    ///
    /// Currently USB-only — there is no BLE implementation of the
    /// keymap write yet (BleDevice falls back to the trait default
    /// which errors). Caller guards on `transport == "USB"`. See
    /// CHANGELOG TODO for the BLE keymap reverse-engineering task.
    fn apply_fn_remaps(cfg: &config::Config, dev: &mut dyn JoroDevice) {
        // Iterate BOTH `fn_remap` (explicit firmware-layer config) AND
        // `fn_host_remap` (host-side preference). On transports where
        // firmware Hypershift writes work (USB, DONGLE), we promote the
        // host-side preferences to firmware writes — Joro's firmware then
        // emits the remapped output directly when Fn+key is pressed,
        // independent of any Fn-state detection at the host level. This
        // is critical for dongle transport where the Fn modifier is
        // invisible to host hooks.

        // Required-for-dongle precursor. No-op on USB/BLE (default trait impl).
        // Without this, dongle silently drops cmd=0x0d writes.
        if let Err(e) = dev.unlock_keymap_writes() {
            eprintln!("Warning: unlock_keymap_writes failed (continuing): {e}");
        }

        // Collected (matrix, modifier, dst) triples — fed to
        // `persist_keymap` so the dongle backend can flush them to flash.
        let mut bindings: Vec<(u8, u8, u8)> = Vec::new();
        let entries = cfg.fn_remap.iter().chain(cfg.fn_host_remap.iter());
        for entry in entries {
            let from = entry.from.trim();
            let to = entry.to.trim();
            if from.is_empty() || to.is_empty() {
                continue;
            }
            // Source: must be a single key with a known Joro matrix index
            let src_matrix = match keys::key_name_to_matrix(from) {
                Some(m) => m,
                None => {
                    eprintln!(
                        "Warning: fn_remap '{from}' → '{to}' — source key has no known matrix index, skipping"
                    );
                    continue;
                }
            };
            // Output: parse as plain key OR combo (modifier+key)
            let (modifier_byte, dst_usage) = match parse_hid_combo(to) {
                Some(p) => p,
                None => {
                    eprintln!(
                        "Warning: fn_remap '{from}' → '{to}' — output not parseable, skipping"
                    );
                    continue;
                }
            };
            match dev.set_layer_remap(src_matrix, modifier_byte, dst_usage) {
                Ok(()) => {
                    eprintln!(
                        "joro-daemon: fn-layer {from} → {to} (matrix=0x{src_matrix:02x} mod=0x{modifier_byte:02x} usage=0x{dst_usage:02x})"
                    );
                    bindings.push((src_matrix, modifier_byte, dst_usage));
                }
                Err(e) => eprintln!("Warning: fn_remap {from} → {to} failed: {e}"),
            }
        }

        // Commit the live (RAM) Hypershift keymap to flash so it survives
        // a power cycle. No-op on USB/BLE (trait default); on the dongle
        // this replays Synapse's proven class-0x0F VARSTORE transaction.
        // Skipped when nothing was written (avoids a needless ~639-frame
        // flash transaction on every config apply).
        if !bindings.is_empty() {
            match dev.persist_keymap(&bindings) {
                Ok(()) => eprintln!(
                    "joro-daemon: Hypershift persisted to flash ({} bindings)",
                    bindings.len()
                ),
                Err(e) => eprintln!("Warning: persist_keymap failed (remaps still live in RAM): {e}"),
            }
        }
    }

    /// Apply the current config to a connected device. Static method so it can
    /// be called with `&self.config` and `&mut *self.device` without borrow conflicts.
    /// Push the current lighting + firmware-keymap state to the device.
    ///
    /// Skips the lighting writes entirely when the `(mode, color,
    /// brightness)` tuple matches `last_applied_lighting` — the keyboard
    /// firmware persists its own lighting state, so re-sending on every
    /// reconnect is redundant and, more importantly, burns the risky
    /// first-write slot on a freshly-ready BLE link where writes often
    /// fail with E_FAIL until the session fully settles. Updates the
    /// cache only after a successful write.
    fn apply_config(&mut self, dev: &mut dyn JoroDevice) {
        let cfg = &self.config;
        let signature = (
            cfg.lighting.mode.clone(),
            cfg.lighting.color.clone(),
            cfg.lighting.brightness,
        );
        let already_applied = self.last_applied_lighting.as_ref() == Some(&signature);
        if already_applied {
            eprintln!("joro-daemon: lighting unchanged since last apply — skipping writes");
        } else {
            let rgb = cfg.lighting.parse_color().ok();
            let lighting_ok = match cfg.lighting.mode.as_str() {
                "breathing" => {
                    if let Some((r, g, b)) = rgb {
                        match dev.set_effect_breathing(r, g, b) {
                            Ok(()) => true,
                            Err(e) => {
                                eprintln!("Warning: set_effect_breathing failed: {e}");
                                false
                            }
                        }
                    } else { false }
                }
                "spectrum" => match dev.set_effect_spectrum() {
                    Ok(()) => true,
                    Err(e) => {
                        eprintln!("Warning: set_effect_spectrum failed: {e}");
                        false
                    }
                },
                _ => {
                    // "static" or unknown — fall back to static color
                    if let Some((r, g, b)) = rgb {
                        match dev.set_static_color(r, g, b) {
                            Ok(()) => true,
                            Err(e) => {
                                eprintln!("Warning: set_static_color failed: {e}");
                                false
                            }
                        }
                    } else { false }
                }
            };
            let brightness_ok = match dev.set_brightness(cfg.lighting.brightness) {
                Ok(()) => true,
                Err(e) => {
                    eprintln!("Warning: set_brightness failed: {e}");
                    false
                }
            };
            eprintln!(
                "joro-daemon: lighting write [{}] mode={} color={} brightness={} — \
                 lighting_ok={lighting_ok} brightness_ok={brightness_ok}",
                dev.transport_name(),
                cfg.lighting.mode,
                cfg.lighting.color,
                cfg.lighting.brightness,
            );
            if lighting_ok && brightness_ok {
                self.last_applied_lighting = Some(signature);
            }
        }

        // Apply firmware keymap entries (single-key remaps only; combos handled by host hook).
        // BLE backend treats this as a no-op.
        for remap in &cfg.remap {
            if remap.to.contains('+') {
                continue; // combo — host hook handles this
            }
            if let (Some(index), Some(usage)) = (
                remap.matrix_index,
                keys::key_name_to_hid(&remap.to),
            ) {
                if let Err(e) = dev.set_keymap_entry(index, usage) {
                    eprintln!("Warning: set_keymap_entry failed for '{}': {e}", remap.name);
                }
            }
        }
    }

    /// Poll the device connection state. Reconnect if lost; disconnect if gone.
    ///
    /// Note (2026-05-21): tried adding an auto-hot-swap path that called
    /// HidApi::new() every tick to detect a Joro switch flip (wired↔wireless)
    /// and re-attach. That blocked the main event loop during USB hot-unplug
    /// events — HidApi enumeration on Windows during a USB transition can
    /// take seconds — producing a "not responding" white-screen webview. The
    /// auto-hop was reverted. Users wanting to switch transports without a
    /// daemon restart can click the **Reconnect** button in the connection-
    /// options modal (Settings → click for options → Reconnect), which drops
    /// the current device and re-runs `try_connect` synchronously.
    fn check_device(&mut self) {
        if let Some(ref mut dev) = self.device {
            if !dev.is_connected() {
                eprintln!("joro-daemon: {} device disconnected", dev.transport_name());
                self.transport_state
                    .store(transport_code::NONE, std::sync::atomic::Ordering::Release);
                self.device = None;
                self.cached_battery = None;
                // Stop the consumer hook — it'll be restarted on reconnect
                self.consumer_hook = None;
                // Release filter-driver hooks — will be re-opened on reconnect
                // if config still enables them. Dropping closes the handle
                // which tears down the rules in the driver.
                self.rzcontrol = None;
                if let Some(ref mut tray) = self.tray {
                    tray.set_connected(false, None, None);
                }
                // If the settings window is open, push the disconnected state
                if self.settings.is_some() {
                    self.push_settings_state();
                }
            }
            return;
        }

        // Not connected — rate-limit reconnect attempts. The periodic
        // reconnect uses the no-BLE probe path (dongle heartbeat ~2s + USB
        // enum), so a 5s cadence is safe — it never touches the slow BLE
        // WinRT scan (that's reserved for the user-initiated Reconnect
        // button, which runs on a background thread).
        const RECONNECT_INTERVAL: Duration = Duration::from_secs(5);
        let now = Instant::now();
        if let Some(last) = self.last_reconnect_attempt {
            if now.duration_since(last) < RECONNECT_INTERVAL {
                return;
            }
        }
        self.last_reconnect_attempt = Some(now);
        // Reconnect via the background probe thread — covers dongle, wired
        // AND BLE without ever blocking the main event loop. (Earlier the
        // periodic path called a synchronous no-BLE try_connect to dodge
        // the webview freeze; now that the probe is threaded we can do the
        // full transport scan automatically — no manual Reprobe needed.)
        self.spawn_reconnect_probe();
    }

    /// Check if the config file has been modified; reload if so.
    fn check_config_changed(&mut self) {
        let mtime = std::fs::metadata(&self.config_path)
            .ok()
            .and_then(|m| m.modified().ok());
        if mtime != self.config_modified {
            eprintln!("joro-daemon: config changed, reloading");
            self.config_modified = mtime;
            self.reload_config();
        }
    }

    /// Re-read config, update remap table, and reapply to device if connected.
    fn reload_config(&mut self) {
        match config::Config::load(&self.config_path) {
            Ok(cfg) => {
                self.config = cfg;
            }
            Err(e) => {
                eprintln!("Warning: failed to reload config: {e}");
                return;
            }
        }

        // Rebuild remap tables
        let (combo_table, trigger_table, special_table, consumer_table) =
            remap::build_remap_tables(&self.config.remap);
        let fn_host_table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);
        remap::update_special_action_table(special_table);
        remap::update_consumer_action_table(consumer_table);
        remap::update_fn_host_remap_table(fn_host_table);

        // Reapply to device if connected. Take the device out briefly so
        // apply_config (which needs &mut self for the last-applied cache)
        // can run without conflicting with &mut self.device.
        if let Some(mut dev) = self.device.take() {
            self.apply_config(&mut *dev);
            self.device = Some(dev);
        }

        // Sync filter-driver hooks to the new ble_fn_primary value.
        self.sync_rzcontrol();

        // Sync the tray submenu checkmarks
        if let Some(ref tray) = self.tray {
            tray.sync_check_state(
                &self.config.lighting.color,
                self.config.lighting.brightness,
                &self.config.lighting.mode,
            );
        }
    }

    /// Handle tray menu events.
    fn handle_menu_events(&mut self, event_loop: &ActiveEventLoop) {
        // Left-click on the tray icon opens the settings window.
        // `with_menu_on_left_click(false)` suppresses the default menu
        // behavior for left click; we handle the click explicitly here.
        while let Some(event) = tray::poll_tray_event() {
            if let tray_icon::TrayIconEvent::Click {
                button: tray_icon::MouseButton::Left,
                button_state: tray_icon::MouseButtonState::Up,
                ..
            } = event
            {
                self.open_settings(event_loop);
            }
        }

        while let Some(event) = tray::poll_menu_event() {
            let id = &event.id;

            // Static menu items (settings/quit/reload/open/autostart)
            let static_action = if let Some(ref tray) = self.tray {
                if id == &tray.menu_quit_id {
                    Some(StaticMenu::Quit)
                } else if id == &tray.menu_settings_id {
                    Some(StaticMenu::Settings)
                } else if id == &tray.menu_reload_id {
                    Some(StaticMenu::Reload)
                } else if id == &tray.menu_open_config_id {
                    Some(StaticMenu::OpenConfig)
                } else if id == &tray.menu_autostart_id {
                    Some(StaticMenu::ToggleAutostart)
                } else {
                    None
                }
            } else {
                None
            };
            if let Some(action) = static_action {
                match action {
                    StaticMenu::Quit => {
                        eprintln!("joro-daemon: quit requested");
                        self.shutdown_and_exit(event_loop);
                    }
                    StaticMenu::Settings => {
                        self.open_settings(event_loop);
                    }
                    StaticMenu::Reload => {
                        eprintln!("joro-daemon: manual config reload");
                        self.reload_config();
                    }
                    StaticMenu::OpenConfig => {
                        let path = self.config_path.to_string_lossy().to_string();
                        let _ = std::process::Command::new("cmd")
                            .args(["/C", "start", "", &path])
                            .spawn();
                    }
                    StaticMenu::ToggleAutostart => {
                        if let Some(ref tray) = self.tray {
                            tray.toggle_autostart();
                        }
                    }
                }
                continue;
            }

            // Preset submenus
            let preset = if let Some(ref tray) = self.tray {
                if let Some((hex, _rgb)) = tray.match_color(id) {
                    Some(Preset::Color(hex))
                } else if let Some(level) = tray.match_brightness(id) {
                    Some(Preset::Brightness(level))
                } else if let Some(mode) = tray.match_effect(id) {
                    Some(Preset::Effect(mode))
                } else {
                    None
                }
            } else {
                None
            };

            if let Some(p) = preset {
                self.apply_preset(p);
            }
        }
    }

    /// Apply a tray preset: update config in-memory, write to file, apply only
    /// the one thing that changed to the device (not the full config). This
    /// minimizes the amount of blocking GATT work we do on the main thread.
    fn apply_preset(&mut self, preset: Preset) {
        // Capture the scalar action + file field so we can drop the `preset`
        // borrow before touching `self.device` / `self.tray`.
        let (log_msg, field_name, field_value) = match preset {
            Preset::Color(hex) => {
                self.config.lighting.color = hex.to_string();
                (
                    format!("preset color {}", hex),
                    "color",
                    format!("\"{}\"", hex),
                )
            }
            Preset::Brightness(level) => {
                self.config.lighting.brightness = level;
                (
                    format!("preset brightness {}", level),
                    "brightness",
                    level.to_string(),
                )
            }
            Preset::Effect(mode) => {
                self.config.lighting.mode = mode.to_string();
                (format!("preset effect {}", mode), "mode", format!("\"{}\"", mode))
            }
        };
        eprintln!("joro-daemon: {}", log_msg);

        eprintln!("joro-daemon:   writing config.toml...");
        if let Err(e) = config::save_lighting_field(&self.config_path, field_name, &field_value) {
            eprintln!("Warning: save {} failed: {e}", field_name);
        }

        // Apply ONLY the changed field to the device (not the full config).
        // apply_config would send both color+brightness+effect which is ~4s of
        // blocking GATT work; doing one write pair keeps us around ~1s.
        if let Some(ref mut dev) = self.device {
            eprintln!("joro-daemon:   applying to device...");
            let cfg = &self.config;
            let result = match preset {
                Preset::Color(_) => {
                    // Respect the current mode when setting color
                    let rgb = cfg.lighting.parse_color().ok();
                    match cfg.lighting.mode.as_str() {
                        "breathing" => rgb.map(|(r, g, b)| dev.set_effect_breathing(r, g, b)),
                        "spectrum" => Some(dev.set_effect_spectrum()),
                        _ => rgb.map(|(r, g, b)| dev.set_static_color(r, g, b)),
                    }
                    .unwrap_or(Ok(()))
                }
                Preset::Brightness(level) => dev.set_brightness(level),
                Preset::Effect(_) => {
                    let rgb = cfg.lighting.parse_color().ok();
                    match cfg.lighting.mode.as_str() {
                        "breathing" => rgb.map(|(r, g, b)| dev.set_effect_breathing(r, g, b)),
                        "spectrum" => Some(dev.set_effect_spectrum()),
                        _ => rgb.map(|(r, g, b)| dev.set_static_color(r, g, b)),
                    }
                    .unwrap_or(Ok(()))
                }
            };
            if let Err(e) = result {
                eprintln!("Warning: preset apply failed: {e}");
            } else {
                eprintln!("joro-daemon:   applied OK");
            }
        }

        eprintln!("joro-daemon:   syncing tray...");
        if let Some(ref tray) = self.tray {
            tray.sync_check_state(
                &self.config.lighting.color,
                self.config.lighting.brightness,
                &self.config.lighting.mode,
            );
        }

        // We just wrote the config file; update the mtime watermark so the
        // next config poll doesn't detect our own write as an external change.
        self.config_modified = std::fs::metadata(&self.config_path)
            .ok()
            .and_then(|m| m.modified().ok());
        eprintln!("joro-daemon:   preset done");
    }

    /// Forcibly drop the BLE device (running its Drop impl to release the
    /// WinRT session) then exit the process. event_loop.exit() alone is not
    /// always honored by winit when a webview / tray icon is still registered
    /// — the main thread stays in run_app forever. Explicitly dropping the
    /// device first ensures Windows releases the keyboard, then we hard-exit.
    fn shutdown_and_exit(&mut self, event_loop: &ActiveEventLoop) {
        remap::remove_hook();
        // Drop the settings webview first (so its HWND parent is still alive)
        if let Some(s) = self.settings.take() {
            drop(s);
        }
        // Drop the BLE/USB device — this runs BleDevice::Drop which closes
        // the WinRT device handle so Windows releases the keyboard cleanly.
        if let Some(d) = self.device.take() {
            drop(d);
        }
        // Ask winit to exit (may or may not actually return from run_app)
        event_loop.exit();
        // Hard-exit so the process terminates even if winit is stuck.
        // BLE cleanup already happened via the explicit drops above.
        std::process::exit(0);
    }

    /// Open the settings window, or focus it if already open.
    fn open_settings(&mut self, event_loop: &ActiveEventLoop) {
        if let Some(ref existing) = self.settings {
            existing.focus();
            return;
        }
        match settings_window::SettingsWindow::new(event_loop, self.proxy.clone()) {
            Ok(w) => {
                eprintln!("joro-daemon: settings window opened");
                // Force foreground/topmost-bump immediately on first open so
                // the window doesn't render behind whatever had focus when
                // the user clicked the tray icon.
                w.bring_to_front();
                self.settings = Some(w);
                // The HTML will request initial state via IPC on DOMContentLoaded,
                // so we don't need to push state here. `handle_settings_ipc` will
                // respond with the current remaps when it sees "request_state".
            }
            Err(e) => eprintln!("Warning: failed to open settings window: {e}"),
        }
    }

    /// Send the current full state (remaps + fn_remaps + lighting + battery
    /// + known matrix indices) into the webview.
    fn push_settings_state(&self) {
        let Some(ref s) = self.settings else { return };
        // List of key names whose Joro matrix index we know — the UI uses
        // this to enable/disable keys in the Hypershift view.
        let known_matrix_keys: Vec<&str> = keys::known_matrix_key_names();
        let state = serde_json::json!({
            "remaps": self.config.remap,
            "fn_remaps": self.config.fn_remap,
            "fn_host_remaps": self.config.fn_host_remap,
            "lighting": {
                "color": self.config.lighting.color,
                "brightness": self.config.lighting.brightness,
                "mode": self.config.lighting.mode,
            },
            "battery": self.cached_battery,
            "known_matrix_keys": known_matrix_keys,
            "transport": self.device.as_ref().map(|d| d.transport_name()),
            "firmware_fn_primary": self.firmware_fn_primary,
            "device_mode_config": self.config.device_mode,
        });
        let script = format!("window.joroSetState({});", state);
        if let Err(e) = s.eval(&script) {
            eprintln!("Warning: push state to webview failed: {e}");
        }
    }

    /// Push just the battery update to the webview (used when polling refreshes
    /// the cached value while the settings window is already open).
    fn push_battery_update(&self) {
        let Some(ref s) = self.settings else { return };
        let payload = match self.cached_battery {
            Some(b) => b.to_string(),
            None => "null".to_string(),
        };
        let script = format!("window.joroSetBattery({});", payload);
        let _ = s.eval(&script);
    }


    /// Send a save result (ok or error) to the webview.
    fn push_save_result(&self, ok: bool, error: Option<&str>) {
        let Some(ref s) = self.settings else { return };
        let payload = match error {
            Some(e) => format!("{{\"ok\":{},\"error\":{}}}", ok, serde_json::to_string(e).unwrap()),
            None => format!("{{\"ok\":{}}}", ok),
        };
        let script = format!("window.joroSaveResult({});", payload);
        let _ = s.eval(&script);
    }

    /// Push a dongle pair / unpair result to the webview. The UI's
    /// `window.joroDongleResult({op, ok, message})` callback updates the
    /// transport section's status line + re-enables the buttons.
    fn push_dongle_result(&self, op: &str, ok: bool, message: &str) {
        let Some(ref s) = self.settings else { return };
        let payload = format!(
            "{{\"op\":{},\"ok\":{},\"message\":{}}}",
            serde_json::to_string(op).unwrap(),
            ok,
            serde_json::to_string(message).unwrap()
        );
        let script = format!("window.joroDongleResult && window.joroDongleResult({});", payload);
        let _ = s.eval(&script);
    }

    /// Process an IPC message from the settings webview.
    fn handle_settings_ipc(&mut self, msg: &str) {
        let parsed: Result<serde_json::Value, _> = serde_json::from_str(msg);
        let Ok(val) = parsed else {
            eprintln!("Warning: bad settings IPC JSON: {msg}");
            return;
        };
        let action = val.get("action").and_then(|v| v.as_str()).unwrap_or("");
        match action {
            "request_state" => {
                self.push_settings_state();
            }
            "save_remaps" => {
                let remaps_val = match val.get("remaps") {
                    Some(r) => r,
                    None => {
                        self.push_save_result(false, Some("missing remaps"));
                        return;
                    }
                };
                let new_remaps: Result<Vec<config::RemapConfig>, _> =
                    serde_json::from_value(remaps_val.clone());
                let new_remaps = match new_remaps {
                    Ok(r) => r,
                    Err(e) => {
                        self.push_save_result(false, Some(&format!("parse: {e}")));
                        return;
                    }
                };

                // Update in-memory config
                self.config.remap = new_remaps;

                // Full-serde write — preserves every field (fn_host_remap,
                // fn_remap, lighting, device_mode, etc.). Previously used
                // `save_remaps` which was a partial writer that truncated
                // everything after the first [[remap]] line, silently
                // wiping the user's [[fn_host_remap]] Hypershift prefs on
                // every base-layer save. Fixed 2026-04-15.
                if let Err(e) = config::save_config(&self.config_path, &self.config) {
                    self.push_save_result(false, Some(&e));
                    return;
                }

                // Rebuild host-side remap tables
                let (combo_table, trigger_table, special_table, consumer_table) =
                    remap::build_remap_tables(&self.config.remap);
                let fn_host_table =
                    remap::build_fn_host_remap_table(&self.config.fn_host_remap);
                remap::update_remap_table(combo_table);
                remap::update_trigger_table(trigger_table);
                remap::update_special_action_table(special_table);
                remap::update_consumer_action_table(consumer_table);
                remap::update_fn_host_remap_table(fn_host_table);

                // NOTE: do NOT call apply_config here. The remap save path
                // only touches `self.config.remap` — re-sending lighting +
                // firmware state would clobber e.g. a user-adjusted backlight
                // set via F10/F11 since the daemon doesn't yet poll keyboard
                // state. Any firmware-level remap changes are handled through
                // the separate `update_fn_remap` path.

                // Bump mtime watermark so the config poller doesn't double-reload
                self.config_modified = std::fs::metadata(&self.config_path)
                    .ok()
                    .and_then(|m| m.modified().ok());

                self.push_save_result(true, None);
                // Push the canonical state back (so the UI matches disk exactly)
                self.push_settings_state();
                eprintln!(
                    "joro-daemon: saved {} remaps from settings window",
                    self.config.remap.len()
                );
            }
            "set_lighting" => {
                // Partial update: any of {color, brightness, mode} may be present.
                let color = val.get("color").and_then(|v| v.as_str()).map(String::from);
                let brightness = val
                    .get("brightness")
                    .and_then(|v| v.as_u64())
                    .map(|b| b.min(255) as u8);
                let mode = val.get("mode").and_then(|v| v.as_str()).map(String::from);
                eprintln!(
                    "joro-daemon: set_lighting IPC — color={color:?} brightness={brightness:?} \
                     mode={mode:?} (device {})",
                    if self.device.is_some() { "present" } else { "NONE" }
                );
                self.apply_lighting_change(color, brightness, mode);
                self.push_save_result(true, None);
            }
            "set_fn_remap" => {
                // Add or update a Fn-layer remap. Replaces any existing entry
                // with the same `from` (case-insensitive).
                let from = val.get("from").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                let to = val.get("to").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                if from.is_empty() || to.is_empty() {
                    self.push_save_result(false, Some("from/to required"));
                    return;
                }
                self.update_fn_remap(from, to);
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            "clear_fn_remap" => {
                let from = val.get("from").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                if from.is_empty() {
                    self.push_save_result(false, Some("from required"));
                    return;
                }
                self.config
                    .fn_remap
                    .retain(|r| !r.from.eq_ignore_ascii_case(&from));
                let _ = config::save_config(&self.config_path, &self.config);
                self.config_modified = std::fs::metadata(&self.config_path)
                    .ok()
                    .and_then(|m| m.modified().ok());
                // Note: we don't have a "clear Fn-layer entry" command; the
                // user would need to re-flash via Synapse to truly clear.
                // Just removing from config means we won't re-apply on next connect.
                eprintln!("joro-daemon: cleared fn_remap from='{from}' (firmware retains until overwritten)");
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            "set_fn_host_remap" => {
                let from = val.get("from").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                let to = val.get("to").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                if from.is_empty() || to.is_empty() {
                    self.push_save_result(false, Some("from/to required"));
                    return;
                }
                self.update_fn_host_remap(from, to);
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            "clear_fn_host_remap" => {
                let from = val.get("from").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
                if from.is_empty() {
                    self.push_save_result(false, Some("from required"));
                    return;
                }
                self.config
                    .fn_host_remap
                    .retain(|r| !r.from.eq_ignore_ascii_case(&from));
                let _ = config::save_config(&self.config_path, &self.config);
                self.config_modified = std::fs::metadata(&self.config_path)
                    .ok()
                    .and_then(|m| m.modified().ok());
                // Rebuild the host-side Fn-layer table so the hook stops
                // translating the removed binding immediately.
                let table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
                remap::update_fn_host_remap_table(table);
                eprintln!("joro-daemon: cleared fn_host_remap from='{from}'");
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            "dongle_pair" => {
                // The pair flow takes ~6s (3s pre-flight + ~3s frame replay
                // including the 2.5s 0b:03 discovery wait). Run on a
                // background thread so we don't block the event loop / UI.
                eprintln!("joro-daemon: dongle_pair IPC — spawning background thread");
                std::thread::spawn(|| {
                    let result = dongle_pair::pair();
                    let (ok, message) = match result {
                        Ok(msg) => (true, msg),
                        Err(e) => (false, e),
                    };
                    post_user_event(UserEvent::DonglePairResult { ok, message });
                });
                // Note: NO push_save_result here — the background thread
                // posts the actual result when the pair finishes.
            }
            "dongle_unpair" => {
                eprintln!("joro-daemon: dongle_unpair IPC — spawning background thread");
                std::thread::spawn(|| {
                    let result = dongle_pair::unpair();
                    let (ok, message) = match result {
                        Ok(msg) => (true, msg),
                        Err(e) => (false, e),
                    };
                    post_user_event(UserEvent::DongleUnpairResult { ok, message });
                });
            }
            "reconnect_device" => {
                // Manual re-probe (background thread — never blocks the UI).
                eprintln!("joro-daemon: reconnect_device IPC — dropping device + spawning probe thread");
                self.device = None;
                self.cached_battery = None;
                self.consumer_hook = None;
                self.rzcontrol = None;
                if let Some(ref mut tray) = self.tray {
                    tray.set_connected(false, None, None);
                }
                fn_detect::reset();
                self.spawn_reconnect_probe();
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            "set_device_mode_pref" => {
                // User clicked Multimedia keys / Function keys in the UI.
                // Auto was removed 2026-05-21 — the auto-detect rule (force
                // MM when any Win+X remap is present) now LOCKS the toggle
                // in the UI instead of being a separate user-selectable
                // option. Accepted values: "mm" | "fn".
                let mode = val
                    .get("mode")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                if !matches!(mode.as_str(), "fn" | "mm") {
                    self.push_save_result(false, Some("mode must be fn|mm"));
                    return;
                }
                // Honor the user's explicit choice. The UI surfaces a
                // warning if their config has Win+X remaps (Lock/Copilot
                // won't work in Fn mode) — but the user gets to decide.
                let effective_mode = mode.as_str();
                self.config.device_mode = effective_mode.to_string();
                if let Err(e) = config::save_config(&self.config_path, &self.config) {
                    self.push_save_result(false, Some(&e));
                    return;
                }
                self.config_modified = std::fs::metadata(&self.config_path)
                    .ok()
                    .and_then(|m| m.modified().ok());
                let fn_primary = effective_mode == "fn";
                if let Some(ref mut dev) = self.device {
                    match dev.set_device_mode(fn_primary) {
                        Ok(()) => {
                            eprintln!(
                                "joro-daemon: device mode pref = {effective_mode}"
                            );
                            self.firmware_fn_primary = Some(fn_primary);
                        }
                        Err(e) => {
                            eprintln!("joro-daemon: set_device_mode failed: {e}");
                            self.push_save_result(false, Some(&format!("device write: {e}")));
                            return;
                        }
                    }
                }
                self.push_settings_state();
                self.push_save_result(true, None);
            }
            other => {
                eprintln!("Warning: unknown settings action: {other}");
            }
        }
    }

    /// Update one or more lighting fields, save to disk, apply to device,
    /// and sync the tray submenu checkmarks. Called from the webview via
    /// the `set_lighting` IPC action.
    fn apply_lighting_change(
        &mut self,
        color: Option<String>,
        brightness: Option<u8>,
        mode: Option<String>,
    ) {
        if let Some(c) = color {
            self.config.lighting.color = c;
            let _ = config::save_lighting_field(
                &self.config_path,
                "color",
                &format!("\"{}\"", self.config.lighting.color),
            );
        }
        if let Some(b) = brightness {
            self.config.lighting.brightness = b;
            let _ = config::save_lighting_field(
                &self.config_path,
                "brightness",
                &b.to_string(),
            );
        }
        if let Some(m) = mode {
            self.config.lighting.mode = m;
            let _ = config::save_lighting_field(
                &self.config_path,
                "mode",
                &format!("\"{}\"", self.config.lighting.mode),
            );
        }

        // Apply to device — apply_config handles mode branching. Take
        // the device out briefly so apply_config (&mut self) can run
        // without conflicting with &mut self.device.
        if let Some(mut dev) = self.device.take() {
            self.apply_config(&mut *dev);
            self.device = Some(dev);
        }

        // Sync the tray submenu checkmarks
        if let Some(ref tray) = self.tray {
            tray.sync_check_state(
                &self.config.lighting.color,
                self.config.lighting.brightness,
                &self.config.lighting.mode,
            );
        }

        // Update mtime watermark so config poller doesn't re-reload
        self.config_modified = std::fs::metadata(&self.config_path)
            .ok()
            .and_then(|m| m.modified().ok());
    }
}

// ── ApplicationHandler ────────────────────────────────────────────────────────

impl ApplicationHandler<UserEvent> for App {
    fn resumed(&mut self, event_loop: &ActiveEventLoop) {
        // Create a hidden window to keep the event loop alive (winit exits without windows)
        if self._window.is_none() {
            let attrs = winit::window::Window::default_attributes()
                .with_visible(false)
                .with_title("joro-daemon");
            self._window = event_loop.create_window(attrs).ok();
        }

        // Create the tray icon
        self.tray = Some(tray::JoroTray::new());

        // Install keyboard hook
        if let Err(e) = remap::install_hook() {
            eprintln!("Warning: failed to install keyboard hook: {e}");
        }

        // Build initial remap tables
        let (combo_table, trigger_table, special_table, consumer_table) =
            remap::build_remap_tables(&self.config.remap);
        let fn_host_table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
        eprintln!(
            "joro-daemon: {} combo remaps, {} trigger remaps, {} fn-host remaps, {} special actions, {} consumer actions",
            combo_table.len(),
            trigger_table.len(),
            fn_host_table.len(),
            special_table.len(),
            consumer_table.len(),
        );
        for t in &trigger_table {
            eprintln!("  trigger: gate=0x{:04X} trigger=0x{:04X} prefix={:?} -> mods={:?} key=0x{:04X}",
                t.gate_mod_vk, t.trigger_vk, t.prefix_mods, t.output_mods, t.output_key);
        }
        for f in &fn_host_table {
            eprintln!(
                "  fn-host: from=0x{:04X} -> mods={:?} key=0x{:04X}",
                f.from_vk, f.modifier_vks, f.key_vk
            );
        }
        for s in &special_table {
            eprintln!("  special: from=0x{:04X} -> {:?}", s.from_vk, s.action);
        }
        for c in &consumer_table {
            eprintln!("  consumer: usage=0x{:04x} -> {}", c.usage, c.label);
        }
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);
        remap::update_consumer_action_table(consumer_table);
        remap::update_special_action_table(special_table);
        remap::update_fn_host_remap_table(fn_host_table);
        // Seed last-known keyboard backlight level so relative Backlight±
        // actions start from the current config value, not the default.
        remap::set_last_backlight(self.config.lighting.brightness);
        // Debug log is opt-in (file I/O per keystroke is too expensive for
        // production). Enable explicitly via the diagnostic CLI subcommand
        // when reproducing key-routing issues.
        remap::set_debug_log(false);

        // Fn-state HID reader. Enumerates Joro HID collections, opens the
        // vendor collection (usage 0x0001/0x0000), watches for Fn press /
        // release events (report `05 04 01` / `05 04 00`, verified over BLE
        // 2026-04-14), and updates `fn_detect::FN_HELD` for the hook.
        // Idempotent — safe to call again on device connect to pick up
        // collections that become visible after a transport change.
        fn_detect::start();

        // RawInput-based Fn detection (DISABLED 2026-05-01) — the consumer-
        // page subscription with RIDEV_INPUTSINK was dropping keys under
        // load. Replacement: consumer_hook.rs (already running for other
        // consumer remaps) now ALSO updates fn_detect::FN_HELD when it
        // sees the consumer 0x029D Fn signal via hidapi — same path BLE
        // uses, no RawInput needed.
        // fn_detect_rawinput::start();
        let _ = fn_detect_rawinput::start; // keep mod alive

        // Try initial device connection
        self.try_connect();

        // Start the background transport-monitor thread (detects Joro
        // hardware-switch flips, wired↔wireless, without blocking the UI).
        self.spawn_transport_monitor();

        // Activate the Razer filter driver (fn-primary over BLE) if
        // enabled in config. Not gated on the daemon's own BLE attach —
        // rzcontrol is PnP-level and works even if our GATT session
        // hasn't come up yet.
        self.sync_rzcontrol();
    }

    fn window_event(
        &mut self,
        _event_loop: &ActiveEventLoop,
        window_id: WindowId,
        event: WindowEvent,
    ) {
        // Route events that belong to the settings window
        let is_settings = self
            .settings
            .as_ref()
            .map(|s| s.id() == window_id)
            .unwrap_or(false);
        if !is_settings {
            return;
        }
        match event {
            WindowEvent::CloseRequested => {
                eprintln!("joro-daemon: settings window closed");
                if let Some(ref s) = self.settings {
                    s.save_position();
                }
                self.settings = None;
            }
            WindowEvent::Resized(size) => {
                if let Some(ref s) = self.settings {
                    s.on_resized(size.width, size.height);
                }
            }
            _ => {}
        }
    }

    fn user_event(&mut self, event_loop: &ActiveEventLoop, event: UserEvent) {
        match event {
            UserEvent::SettingsIpc(msg) => self.handle_settings_ipc(&msg),
            UserEvent::CtrlC => {
                eprintln!("joro-daemon: Ctrl+C received, shutting down cleanly");
                self.shutdown_and_exit(event_loop);
            }
            UserEvent::BacklightObserved(level) => {
                if self.config.lighting.brightness == level {
                    eprintln!(
                        "joro-daemon: backlight observed {level} — no-op (config already {level})",
                    );
                    return;
                }
                eprintln!(
                    "joro-daemon: backlight observed {level} (hardware MM key) — was {}",
                    self.config.lighting.brightness
                );
                self.config.lighting.brightness = level;
                remap::set_last_backlight(level);
                if let Err(e) = config::save_config(&self.config_path, &self.config) {
                    eprintln!("Warning: save_config failed: {e}");
                }
                self.config_modified = std::fs::metadata(&self.config_path)
                    .ok()
                    .and_then(|m| m.modified().ok());
                if self.settings.is_some() {
                    eprintln!(
                        "joro-daemon: backlight observed → pushing state to settings (level={level})"
                    );
                    self.push_settings_state();
                } else {
                    eprintln!(
                        "joro-daemon: backlight observed but no settings window — UI won't update (level={level})"
                    );
                }
            }
            UserEvent::BatteryObserved(pct) => {
                // A battery reading means fn_detect just saw a Joro heartbeat
                // (`09 31 …` on the dongle, or the BLE battery characteristic).
                // If the daemon currently has NO device attached, that
                // heartbeat is proof a transport is live RIGHT NOW — kick a
                // reconnect probe immediately rather than waiting for the
                // next periodic tick. Fixes the post-pair window where the
                // tray showed "disconnected" until the user typed (the
                // one-shot dongle heartbeat probe during try_connect had
                // missed the keyboard's first heartbeat; fn_detect's
                // continuous reader catches it and we react here).
                if self.device.is_none() {
                    eprintln!("joro-daemon: heartbeat observed while disconnected — kicking reconnect");
                    self.last_reconnect_attempt = Some(Instant::now());
                    self.spawn_reconnect_probe();
                }
                // Passive battery readout from the dongle heartbeat. Pure
                // display — never touches connection state. Only push when
                // the value actually changes to avoid webview spam (the
                // heartbeat fires every few seconds).
                if self.cached_battery != Some(pct) {
                    self.cached_battery = Some(pct);
                    eprintln!("joro-daemon: battery {pct}% (passive heartbeat)");
                    self.push_battery_update();
                }
            }
            UserEvent::BacklightSet(level) => {
                eprintln!("joro-daemon: BacklightSet({level}) event received");
                if let Some(ref mut dev) = self.device {
                    match dev.set_brightness(level) {
                        Ok(()) => {
                            eprintln!("joro-daemon: backlight -> {level} applied");
                            self.config.lighting.brightness = level;
                            // Persist so the new value survives daemon restart.
                            // Bump the config-poller mtime watermark so our own
                            // write doesn't trigger a reload-and-rebuild loop.
                            if let Err(e) = config::save_config(&self.config_path, &self.config) {
                                eprintln!("Warning: save_config failed: {e}");
                            }
                            self.config_modified = std::fs::metadata(&self.config_path)
                                .ok()
                                .and_then(|m| m.modified().ok());
                            if self.settings.is_some() { self.push_settings_state(); }
                        }
                        Err(e) => eprintln!("joro-daemon: backlight set({level}) failed: {e}"),
                    }
                } else {
                    eprintln!("joro-daemon: BacklightSet dropped -- no device connected");
                }
            }
            UserEvent::DonglePairResult { ok, message } => {
                eprintln!("joro-daemon: dongle pair result ok={ok} msg={message}");
                self.push_dongle_result("pair", ok, &message);
                if ok {
                    // The new bond may have triggered a transport change.
                    // Drop the current device handle (likely BLE) so the
                    // PERIODIC reconnect (about_to_wait → check_for_reconnect
                    // every 10s, RECONNECT_INTERVAL throttle) picks it up.
                    //
                    // CRITICAL: do NOT call try_connect immediately. The
                    // 70-frame Synapse replay leaves the dongle's radio
                    // buffer saturated for several seconds; an immediate
                    // apply_config burst (lighting + mode + idle-timer
                    // writes) on top of that piles into the same buffer
                    // → severe input lag for ~10s while the keyboard's
                    // HID interrupt-IN pipeline starves behind our
                    // queued control writes. Letting the periodic poll
                    // catch up (10s later) gives the radio time to
                    // settle and produces a clean, lag-free reconnect.
                    self.device = None;
                    self.last_reconnect_attempt = Some(Instant::now());
                    self.push_settings_state();
                }
            }
            UserEvent::TransportChanged => {
                // Joro hardware switch flipped (wired↔wireless). Drop the
                // now-stale device handle and re-probe. Setting transport_state
                // to NONE stops the monitor from re-posting while we reconnect.
                eprintln!("joro-daemon: TransportChanged — dropping stale device, re-probing");
                self.transport_state
                    .store(transport_code::NONE, std::sync::atomic::Ordering::Release);
                self.device = None;
                self.cached_battery = None;
                self.consumer_hook = None;
                self.rzcontrol = None;
                if let Some(ref mut tray) = self.tray {
                    tray.set_connected(false, None, None);
                }
                fn_detect::reset();
                self.last_reconnect_attempt = Some(Instant::now());
                self.spawn_reconnect_probe();
                if self.settings.is_some() {
                    self.push_settings_state();
                }
            }
            UserEvent::ReconnectComplete => {
                // Background reconnect probe finished — pick up the device.
                let dev = self.pending_device.lock().unwrap().take();
                match dev {
                    Some(d) => {
                        eprintln!("joro-daemon: ReconnectComplete — probe found a device, finishing");
                        self.finish_connect(d);
                    }
                    None => {
                        eprintln!("joro-daemon: ReconnectComplete — probe found nothing");
                        // Leave device None; periodic check_device will keep
                        // retrying (no-BLE) and the user can Reconnect again.
                        self.last_reconnect_attempt = None;
                        if let Some(ref mut tray) = self.tray {
                            tray.set_connected(false, None, None);
                        }
                        if self.settings.is_some() {
                            self.push_settings_state();
                        }
                    }
                }
            }
            UserEvent::DongleUnpairResult { ok, message } => {
                eprintln!("joro-daemon: dongle unpair result ok={ok} msg={message}");
                self.push_dongle_result("unpair", ok, &message);
                if ok {
                    // For ~6s after we wipe the bond the keyboard's
                    // radio side still thinks it's bonded and keeps
                    // emitting heartbeats through the dongle. Bypass the
                    // dongle probe for the next 10s so the daemon
                    // doesn't immediately re-select dongle on those
                    // stale heartbeats.
                    self.skip_dongle_until = Some(
                        Instant::now() + std::time::Duration::from_secs(10),
                    );
                    // Joro does NOT auto-fall-to-BLE on unpair — Razer FW
                    // puts it in pair-advertise mode (3 lights blinking).
                    // User must tap F1/F2/F3 to activate a BLE host slot.
                    // Until they do, there's no transport to attach to.
                    // Just drop the device and let the periodic poll
                    // detect BLE once the user makes that selection.
                    self.device = None;
                    self.last_reconnect_attempt = Some(Instant::now());
                    self.push_settings_state();
                }
            }
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let now = Instant::now();

        // Device presence check every 3s. For the dongle this is a pure
        // HID enumeration (no USB control traffic) so it's cheap and never
        // starves keyboard input. For wired it's one Protocol30 firmware
        // query — harmless on a direct cable. Connection state = "is the
        // dongle/Joro present", NOT "did a Protocol30 query succeed" through
        // the dongle's bridged RF link (those time out constantly and used
        // to flap the tray red). 3s (was 10s) so a Joro hardware-switch
        // flip — wired↔wireless — is noticed within ~3s instead of ~10s.
        if now.duration_since(self.last_device_poll) >= Duration::from_secs(3) {
            self.last_device_poll = now;
            self.check_device();
        }

        // Poll config file every 5 seconds
        if now.duration_since(self.last_config_poll) >= Duration::from_secs(5) {
            self.last_config_poll = now;
            self.check_config_changed();
        }

        // Battery is read PASSIVELY from the dongle's heartbeat HID report
        // (handled in fn_detect -> UserEvent::BatteryObserved). NO solicited
        // Protocol30 query here — that's what flooded the dongle control
        // pipe and caused input lag. Drain the activity flag so it doesn't
        // accumulate (no longer used for state, kept only as the wake hint
        // fn_detect sets on every heartbeat/keystroke).
        fn_detect::JORO_HID_ACTIVITY.store(false, std::sync::atomic::Ordering::Relaxed);

        // Periodic fn_detect self-heal check. When a reader thread
        // exhausts its error budget (stale HID handle), it removes its
        // path from OPENED_PATHS and exits. `fn_detect::start()` is
        // idempotent — only opens paths not already in OPENED_PATHS —
        // so a periodic call respawns dead readers without disturbing
        // healthy ones. 15 s cadence caps the hypershift downtime at
        // ~15 s after a handle goes stale, with negligible overhead
        // when everything is healthy.
        let should_check_fn = match self.last_fn_detect_check {
            Some(last) => now.duration_since(last) >= Duration::from_secs(15),
            None => true,
        };
        if should_check_fn {
            self.last_fn_detect_check = Some(now);
            fn_detect::start();
        }

        // Handle menu events
        self.handle_menu_events(event_loop);

        // Wake up every 100ms to poll
        event_loop.set_control_flow(ControlFlow::WaitUntil(
            Instant::now() + Duration::from_millis(100),
        ));
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

/// Matrix-index discovery tool. Programs a batch of 26 consecutive Joro
/// matrix indices to emit letters a..z in the **Fn layer only** (base layer
/// is untouched, so normal typing is unaffected). User holds Fn + the
/// unknown physical key in Notepad; the letter that appears identifies the
/// matrix index of that key.
///
/// Batch 0 covers indices 0x01..=0x1A, batch 1 covers 0x1B..=0x34, etc.
/// Run `cargo run -- scan <batch>`. After scanning, use Synapse "Reset
/// Profile" to restore factory Fn-layer defaults, or re-run the daemon
/// normally to reapply your configured fn_remaps.
/// Targeted scan of the KNOWN GAP indices in `JORO_MATRIX_TABLE`.
/// Programs each gap matrix slot to a consecutive letter a..z on the
/// Fn layer so the user can identify which physical key each gap
/// corresponds to by pressing Fn+<key> and watching which letter
/// appears in a text field. 26 slots max — fits the alphabet.
fn run_gap_scan() {
    // Indices that are *between* known-mapped indices in the matrix —
    // candidates for physical keys we haven't identified yet. Ordered
    // by likelihood of being a user-programmable key. Max 26 so a..z
    // cover them 1:1.
    let gaps: [u8; 26] = [
        // Bottom row / Fn-area (between Copilot 0x3E and RCtrl 0x40,
        // and between RCtrl 0x40 and LShift 0x46)
        0x3F, 0x41, 0x42, 0x43, 0x44, 0x45,
        // Arrow/nav cluster gaps
        0x52, 0x57, 0x58,
        // Between Right (0x59) and Insert (0x65) — likely PrintScreen
        // + any other 75% extras
        0x5A, 0x5B, 0x5C, 0x5D, 0x5E, 0x5F, 0x60, 0x61, 0x62, 0x63, 0x64,
        // Between Delete (0x66) and Escape (0x6E)
        0x67, 0x68, 0x69, 0x6A, 0x6B, 0x6C,
    ];

    let mut dev = match usb::RazerDevice::open() {
        Some(d) => d,
        None => {
            eprintln!("scan-gaps: no USB Joro found — scan requires a wired connection.");
            eprintln!("Make sure the daemon isn't already running (it holds USB exclusively).");
            std::process::exit(1);
        }
    };

    println!("\n=== Joro matrix gap scan ===");
    println!("Programming {} known-gap Fn-layer indices to letters a..z", gaps.len());
    println!("(Fn-layer only — base layer NOT modified, normal typing is unaffected)\n");

    for (i, &matrix_idx) in gaps.iter().enumerate() {
        let letter = (b'a' + i as u8) as char;
        let hid_usage = 0x04 + i as u8; // HID usage for 'a'=0x04 .. 'z'=0x1D
        match dev.set_layer_remap(matrix_idx, 0x00, hid_usage) {
            Ok(()) => println!("  matrix 0x{matrix_idx:02x}  →  Fn+<key> emits '{letter}'"),
            Err(e) => eprintln!("  matrix 0x{matrix_idx:02x} program FAILED: {e}"),
        }
    }

    println!("\n── Instructions ──");
    println!("1. IMPORTANT: cycle the Joro transport wired↔BLE↔wired once — the");
    println!("   firmware stores our writes but only refreshes the runtime");
    println!("   Hypershift table on a transport change (see memory");
    println!("   project_hypershift_commit_trigger.md).");
    println!("2. Open Notepad (or any text field).");
    println!("3. Hold Fn and press EVERY physical key on the keyboard you can");
    println!("   find, including ones you haven't identified yet. Note which");
    println!("   letter appears for each key.");
    println!("4. Tell Claude the letter→key mapping. Example:");
    println!("     'a = LAlt, b = Fn, c = nothing, d = PrintScreen, ...'");
    println!("5. Keys NOT in this scan's range will emit their normal Hypershift");
    println!("   output (the user's existing fn_remap bindings or media keys).\n");

    println!("Matrix index lookup:");
    for (i, &matrix_idx) in gaps.iter().enumerate() {
        let letter = (b'a' + i as u8) as char;
        println!("  '{letter}' = 0x{matrix_idx:02x}");
    }

    println!("\nPress Enter here when done. Daemon will release USB and you can");
    println!("re-run it normally (which reapplies your configured fn_remap).");
    let mut s = String::new();
    let _ = std::io::stdin().read_line(&mut s);
    println!("scan-gaps: done. Device released.");
}

fn run_matrix_scan(batch: u8) {
    let start: u16 = 1 + (batch as u16) * 26;
    let end: u16 = start + 25;
    if end > 0xFF {
        eprintln!("scan: batch {batch} out of range");
        std::process::exit(1);
    }
    let start = start as u8;
    let end = end as u8;

    let mut dev = match usb::RazerDevice::open() {
        Some(d) => d,
        None => {
            eprintln!("scan: no USB Joro found — scan requires a wired connection.");
            eprintln!("Make sure the daemon isn't already running (it holds USB exclusively).");
            std::process::exit(1);
        }
    };

    println!("\n=== Joro matrix scan — batch {batch} ===");
    println!(
        "Programming Fn-layer indices 0x{start:02x}..=0x{end:02x} to letters a..z"
    );
    println!("(Fn-layer only — base layer NOT modified, normal typing is unaffected)\n");

    for i in 0u8..=25 {
        let matrix_idx = start + i;
        let letter = (b'a' + i) as char;
        let hid_usage = 0x04 + i; // HID usage for 'a'=0x04 .. 'z'=0x1D
        match dev.set_layer_remap(matrix_idx, 0x00, hid_usage) {
            Ok(()) => println!("  matrix 0x{matrix_idx:02x}  →  Fn+<key> emits '{letter}'"),
            Err(e) => eprintln!("  matrix 0x{matrix_idx:02x} program FAILED: {e}"),
        }
    }

    println!("\n── Instructions ──");
    println!("1. Open Notepad (or any text field).");
    println!("2. Hold Fn and press the physical keys you want to identify.");
    println!("3. The letter that appears tells you the matrix index:");
    println!(
        "     a = 0x{start:02x},  b = 0x{:02x},  ...,  z = 0x{end:02x}",
        start + 1
    );
    println!("4. When done with this batch, run the next batch:");
    println!("     cargo run -- scan {}", batch + 1);
    println!("5. To restore factory Fn behavior (media keys, etc.), click");
    println!("   'Reset Profile' in Synapse — or just re-run the daemon,");
    println!("   which will reapply your configured [[fn_remap]] entries.");
    println!("\nPress Enter here when finished with this batch...");
    let mut s = String::new();
    let _ = std::io::stdin().read_line(&mut s);
    println!("scan: done. Device released.");
}

fn main() {
    // Release builds have no console — redirect stderr/stdout to a log
    // file so every existing eprintln! is actually recoverable. No-op
    // for debug builds (console is attached and we want live output).
    #[cfg(not(debug_assertions))]
    {
        logfile::init();
        logfile::banner();
    }

    // CLI dispatch — recognise `scan <batch>` before constructing the event loop.
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 2 && args[1] == "scan" {
        let batch: u8 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(0);
        run_matrix_scan(batch);
        return;
    }
    if args.len() >= 2 && args[1] == "scan-gaps" {
        run_gap_scan();
        return;
    }

    // Autostart toggle CLI — lets scripts / the user enable or disable the
    // daemon's HKCU\...\Run\JoroDaemon entry without opening the tray menu.
    // The value is set to the path of THIS binary, so build release and
    // invoke from the release target directory if you want a stable
    // autostart path that survives `cargo clean`.
    if args.len() >= 2 && args[1] == "enable-autostart" {
        tray::enable_autostart();
        if tray::is_autostart_enabled() {
            eprintln!("autostart: enabled (JoroDaemon in HKCU\\...\\Run)");
        } else {
            eprintln!("autostart: FAILED to enable");
            std::process::exit(1);
        }
        return;
    }
    if args.len() >= 2 && args[1] == "disable-autostart" {
        tray::disable_autostart();
        eprintln!("autostart: disabled");
        return;
    }
    // HID report discovery: spawn fn_detect, run until Ctrl+C. Use this to
    // find which HID collection / report byte carries Joro's Fn state
    // (especially over BLE where Windows owns the keyboard HID collection
    // but vendor collections are still readable). Press Fn+key combos while
    // this is running; every raw report is printed with a timestamp.
    if args.len() >= 2 && args[1] == "fn-detect" {
        println!("fn-detect: starting HID report discovery. Press Ctrl+C to stop.");
        println!("fn-detect: press Fn, Fn+A, Fn+Left, plain A, plain Left — watch the output.");
        fn_detect::spawn_diagnostic();
        // Block forever so the diagnostic threads stay alive
        loop {
            std::thread::sleep(std::time::Duration::from_secs(60));
        }
    }
    // brightness probe: test DDC/CI brightness control on external monitors.
    // Usage: `cargo run -- brightness info` / `brightness +10` / `brightness 50`
    if args.len() >= 2 && args[1] == "brightness" {
        let arg = args.get(2).map(|s| s.as_str()).unwrap_or("info");
        if arg == "info" {
            let monitors = brightness::PhysicalMonitor::enumerate();
            eprintln!("brightness: {} DDC/CI-capable monitors", monitors.len());
            for m in &monitors {
                eprintln!("  {}  min={} cur={} max={}", m.friendly, m.min, m.cur, m.max);
            }
            return;
        }
        if arg == "caps" {
            // Read MCCS capability string from every DDC/CI-capable monitor
            // to see which VCP feature codes the monitor advertises.
            let monitors = brightness::PhysicalMonitor::enumerate();
            for m in &monitors {
                eprintln!("\n{}", m.friendly);
                match m.capability_string() {
                    Some(s) => eprintln!("  caps: {}", s),
                    None => eprintln!("  caps: (unavailable)"),
                }
            }
            return;
        }
        if arg == "refresh-reset" {
            // Tell Windows to re-apply the current display mode. Should
            // force the display driver to re-negotiate with the monitor
            // (fresh DDC/CI handshake, re-read EDID) without changing
            // anything visible. Useful for testing whether a mode
            // re-apply clears the post-DPMS-wake brightness lock.
            use windows::Win32::Graphics::Gdi::{
                ChangeDisplaySettingsExW, CDS_TYPE, DEVMODEW,
            };
            const CDS_RESET: u32 = 0x40000000;
            let r = unsafe {
                ChangeDisplaySettingsExW(
                    windows::core::PCWSTR::null(),
                    None::<*const DEVMODEW>,
                    None,
                    CDS_TYPE(CDS_RESET),
                    None,
                )
            };
            eprintln!("brightness: refresh-reset returned {:?} (0=DISP_CHANGE_SUCCESSFUL)", r);
            return;
        }
        if arg == "refresh-list" {
            // List all display adapters/monitors with their current mode.
            // Diagnostic for figuring out which display name targets the
            // physical G91SD.
            use windows::Win32::Graphics::Gdi::{
                EnumDisplayDevicesW, EnumDisplaySettingsW, DEVMODEW,
                DISPLAY_DEVICEW, ENUM_CURRENT_SETTINGS,
            };
            let mut i: u32 = 0;
            loop {
                let mut dd = DISPLAY_DEVICEW::default();
                dd.cb = std::mem::size_of::<DISPLAY_DEVICEW>() as u32;
                let ok = unsafe {
                    EnumDisplayDevicesW(
                        windows::core::PCWSTR::null(),
                        i,
                        &mut dd,
                        0,
                    )
                }.as_bool();
                if !ok { break; }
                let name_w = &dd.DeviceName;
                let name_end = name_w.iter().position(|&c| c == 0).unwrap_or(name_w.len());
                let name = String::from_utf16_lossy(&name_w[..name_end]);
                let str_w = &dd.DeviceString;
                let str_end = str_w.iter().position(|&c| c == 0).unwrap_or(str_w.len());
                let str_ = String::from_utf16_lossy(&str_w[..str_end]);

                let mut dm = DEVMODEW::default();
                dm.dmSize = std::mem::size_of::<DEVMODEW>() as u16;
                let name_pcwstr_buf: Vec<u16> = dd.DeviceName.iter().copied().collect();
                let ok = unsafe {
                    EnumDisplaySettingsW(
                        windows::core::PCWSTR::from_raw(name_pcwstr_buf.as_ptr()),
                        ENUM_CURRENT_SETTINGS,
                        &mut dm,
                    )
                }.as_bool();
                let mode_str = if ok {
                    format!("{}x{} @ {}Hz", dm.dmPelsWidth, dm.dmPelsHeight, dm.dmDisplayFrequency)
                } else {
                    "(no mode)".to_string()
                };
                let active = if dd.StateFlags & 0x01 != 0 { "ACTIVE" } else { "" };
                eprintln!("[{i}] '{name}' '{str_}' {active} — {mode_str}");
                i += 1;
            }
            return;
        }
        if arg == "refresh-toggle" {
            // Toggle refresh rate to an alternate supported rate briefly,
            // then back, targeting the primary active display device
            // explicitly. Forces the driver to push a new signal-format
            // packet to the monitor, triggering a full re-handshake.
            use windows::Win32::Graphics::Gdi::{
                ChangeDisplaySettingsExW, EnumDisplayDevicesW, EnumDisplaySettingsW,
                CDS_TYPE, DEVMODEW, DISPLAY_DEVICEW, DM_DISPLAYFREQUENCY,
                ENUM_CURRENT_SETTINGS,
            };
            // Find the primary active display device.
            let mut primary_name: Vec<u16> = Vec::new();
            let mut i: u32 = 0;
            loop {
                let mut dd = DISPLAY_DEVICEW::default();
                dd.cb = std::mem::size_of::<DISPLAY_DEVICEW>() as u32;
                let ok = unsafe {
                    EnumDisplayDevicesW(
                        windows::core::PCWSTR::null(), i, &mut dd,
                        0,
                    )
                }.as_bool();
                if !ok { break; }
                // DISPLAY_DEVICE_ATTACHED_TO_DESKTOP=0x01, PRIMARY_DEVICE=0x04
                let flags = dd.StateFlags;
                if flags & 0x01 != 0 && flags & 0x04 != 0 {
                    primary_name = dd.DeviceName.iter().copied().collect();
                    break;
                }
                i += 1;
            }
            if primary_name.is_empty() {
                eprintln!("brightness: refresh-toggle: no primary active display found");
                return;
            }
            let name_str: String = {
                let end = primary_name.iter().position(|&c| c == 0).unwrap_or(primary_name.len());
                String::from_utf16_lossy(&primary_name[..end])
            };
            let name_pcwstr = windows::core::PCWSTR::from_raw(primary_name.as_ptr());

            let mut cur_dm = DEVMODEW::default();
            cur_dm.dmSize = std::mem::size_of::<DEVMODEW>() as u16;
            let ok = unsafe {
                EnumDisplaySettingsW(name_pcwstr, ENUM_CURRENT_SETTINGS, &mut cur_dm)
            }.as_bool();
            if !ok {
                eprintln!("brightness: refresh-toggle: EnumDisplaySettings on '{name_str}' failed");
                return;
            }
            let orig_hz = cur_dm.dmDisplayFrequency;
            eprintln!("brightness: refresh-toggle: '{name_str}' current = {}x{} @ {} Hz",
                cur_dm.dmPelsWidth, cur_dm.dmPelsHeight, orig_hz);

            // Find alternate refresh rate at same resolution
            let mut alt_dm = DEVMODEW::default();
            alt_dm.dmSize = std::mem::size_of::<DEVMODEW>() as u16;
            let mut alt_hz: u32 = 0;
            let mut j: u32 = 0;
            loop {
                let ok = unsafe {
                    EnumDisplaySettingsW(
                        name_pcwstr,
                        windows::Win32::Graphics::Gdi::ENUM_DISPLAY_SETTINGS_MODE(j),
                        &mut alt_dm,
                    )
                }.as_bool();
                if !ok { break; }
                if alt_dm.dmPelsWidth == cur_dm.dmPelsWidth
                    && alt_dm.dmPelsHeight == cur_dm.dmPelsHeight
                    && alt_dm.dmDisplayFrequency != orig_hz
                    && alt_dm.dmDisplayFrequency > 0 {
                    alt_hz = alt_dm.dmDisplayFrequency;
                    break;
                }
                j += 1;
                if j > 500 { break; }
            }
            if alt_hz == 0 {
                eprintln!("brightness: refresh-toggle: no alternate refresh rate found");
                return;
            }
            eprintln!("brightness: refresh-toggle: alternate = {} Hz", alt_hz);

            let mut toggle_dm = cur_dm;
            toggle_dm.dmDisplayFrequency = alt_hz;
            toggle_dm.dmFields = DM_DISPLAYFREQUENCY;
            let r1 = unsafe {
                ChangeDisplaySettingsExW(name_pcwstr, Some(&toggle_dm), None, CDS_TYPE(0), None)
            };
            eprintln!("brightness: refresh-toggle: set {} Hz returned {:?}", alt_hz, r1);
            std::thread::sleep(std::time::Duration::from_millis(800));

            let mut back_dm = cur_dm;
            back_dm.dmFields = DM_DISPLAYFREQUENCY;
            let r2 = unsafe {
                ChangeDisplaySettingsExW(name_pcwstr, Some(&back_dm), None, CDS_TYPE(0), None)
            };
            eprintln!("brightness: refresh-toggle: back to {} Hz returned {:?}", orig_hz, r2);
            return;
        }
        if arg == "reinit" {
            // Force the display driver to re-apply its current topology.
            // Goes through a full driver-level re-init that re-reads EDID
            // and re-initializes DDC/CI on the monitor. If the VCP 0x10
            // "silent drop" state is held somewhere in the dxva2/driver
            // layer (not the scaler firmware itself), this should clear
            // it. SDC_APPLY | SDC_USE_DATABASE_CURRENT means "re-apply
            // the last known good config" — no mode change, no
            // user-visible layout change. Screen may flicker briefly.
            use windows::Win32::Devices::Display::{
                SetDisplayConfig, SDC_APPLY, SDC_USE_DATABASE_CURRENT,
                SDC_FORCE_MODE_ENUMERATION, SDC_TOPOLOGY_EXTERNAL,
            };
            let mode = args.get(3).map(|s| s.as_str()).unwrap_or("db");
            let flags = match mode {
                "force" => SDC_APPLY | SDC_TOPOLOGY_EXTERNAL | SDC_FORCE_MODE_ENUMERATION,
                "external" => SDC_APPLY | SDC_TOPOLOGY_EXTERNAL,
                _ => SDC_APPLY | SDC_USE_DATABASE_CURRENT,
            };
            let r = unsafe { SetDisplayConfig(None, None, flags) };
            eprintln!("brightness: SetDisplayConfig(mode={mode}) returned {r} (0=ERROR_SUCCESS)");
            return;
        }
        if arg == "setmon" {
            // `brightness setmon N` — call Windows's SetMonitorBrightness
            // (high-level API) with value N. Different DDC packet path
            // than vcp_set(0x10). Test whether SetVCPFeature's "packet
            // rejected by scaler" class of failures is avoided here.
            let v = args.get(3).and_then(|s| s.parse::<u32>().ok())
                .expect("usage: brightness setmon <0..100>");
            let monitors = brightness::PhysicalMonitor::enumerate();
            for m in &monitors {
                match m.set_monitor_brightness(v) {
                    Ok(()) => eprintln!("{}  SetMonitorBrightness({v}) ok", m.friendly),
                    Err(e) => eprintln!("{}  SetMonitorBrightness({v}) failed: {e}", m.friendly),
                }
            }
            return;
        }
        if arg == "vcp" {
            // `brightness vcp` → dump current value for all standard VCP codes
            // `brightness vcp 10` → read just that one
            // `brightness vcp 10 = 75` → write value 75 to VCP 0x10
            let monitors = brightness::PhysicalMonitor::enumerate();
            let code = args.get(3).and_then(|s| u8::from_str_radix(s, 16).ok());
            if args.get(4).map(|s| s.as_str()) == Some("=") {
                let v = args.get(5).and_then(|s| s.parse::<u32>().ok()).expect("value");
                let c = code.expect("vcp code hex");
                for m in &monitors {
                    match m.vcp_set(c, v) {
                        Ok(()) => eprintln!("{}  VCP 0x{:02x} <= {}", m.friendly, c, v),
                        Err(e) => eprintln!("{}  VCP 0x{:02x} set failed: {e}", m.friendly, c),
                    }
                }
                return;
            }
            for m in &monitors {
                eprintln!("\n{}", m.friendly);
                let codes: Vec<u8> = if let Some(c) = code {
                    vec![c]
                } else {
                    vec![0x02, 0x04, 0x05, 0x06, 0x08, 0x0B, 0x0C, 0x10, 0x12,
                         0x14, 0x16, 0x18, 0x1A, 0x52, 0x60, 0x62, 0x6B, 0x6C,
                         0x8D, 0x8F, 0xCA, 0xD6, 0xDC]
                };
                for c in codes {
                    if let Some((cur, max)) = m.vcp_get(c) {
                        eprintln!("  VCP 0x{c:02x}: cur={cur} max={max}");
                    }
                }
            }
            return;
        }
        // +/- delta in percent (+10, -20, etc.)
        if let Some(rest) = arg.strip_prefix('+') {
            if let Ok(d) = rest.parse::<i32>() { brightness::delta_all(d); return; }
        }
        if arg.starts_with('-') {
            if let Ok(d) = arg.parse::<i32>() { brightness::delta_all(d); return; }
        }
        // absolute percent
        if let Ok(p) = arg.parse::<u32>() { brightness::set_all_percent(p); return; }
        eprintln!("brightness usage: info | caps | vcp [CODE] [= VALUE] | +N | -N | N (0-100)");
        return;
    }

    // set-mode fn|mm — flip Joro firmware fn/mm toggle via BLE Protocol30.
    // See memory/project_fnmm_toggle_solved.md for the decoded command.
    if args.len() >= 2 && args[1] == "set-mode" {
        let mut dev = ble::BleDevice::open().expect("no BLE Joro");
        let fn_primary = match args.get(2).map(|s| s.as_str()) {
            Some("fn") => true,
            Some("mm") => false,
            _ => panic!("set-mode requires 'fn' or 'mm'"),
        };
        match dev.set_device_mode(fn_primary) {
            Ok(()) => eprintln!("set-mode: ok ({})", if fn_primary { "Fn" } else { "MM" }),
            Err(e) => eprintln!("set-mode: err: {e}"),
        }
        match dev.get_device_mode() {
            Ok(is_fn) => eprintln!("set-mode: current = {}", if is_fn { "Fn" } else { "MM" }),
            Err(e) => eprintln!("set-mode: get failed: {e}"),
        }
        return;
    }
    // ble-idle — host-side sleep test over BLE (Phase 2/4 zero-risk):
    //   joro-daemon ble-idle get           -> read class=07 cmd=84
    //   joro-daemon ble-idle <b0> <b1> ... -> SET class=07 cmd=83 data
    // Empirical: try '00 00' (disable/never), 'ff ff', or u16 seconds.
    if args.len() >= 2 && args[1] == "ble-idle" {
        let mut dev = ble::BleDevice::open().expect("no BLE Joro");
        if args.get(2).map(|s| s.as_str()) == Some("get") {
            match dev.get_idle_raw() {
                Ok(v) => eprintln!(
                    "ble-idle get (07:84) = [{}]",
                    v.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(" ")
                ),
                Err(e) => eprintln!("ble-idle get: err {e}"),
            }
            return;
        }
        let bytes: Vec<u8> = args[2..]
            .iter()
            .filter_map(|s| u8::from_str_radix(s.trim_start_matches("0x"), 16).ok())
            .collect();
        if bytes.is_empty() {
            eprintln!("ble-idle: usage: ble-idle get | ble-idle <b0> <b1> [..]");
            return;
        }
        eprintln!(
            "ble-idle SET 07:83 data=[{}]",
            bytes.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(" ")
        );
        match dev.set_idle_raw(&bytes) {
            Ok(()) => eprintln!("ble-idle: SET ok (status success)"),
            Err(e) => eprintln!("ble-idle: SET err {e}"),
        }
        match dev.get_idle_raw() {
            Ok(v) => eprintln!(
                "ble-idle readback (07:84) = [{}]",
                v.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(" ")
            ),
            Err(e) => eprintln!("ble-idle readback: err {e}"),
        }
        return;
    }

    // clear-hypershift — write passthrough Fn-layer entries for
    // arrows on the dongle, undoing whatever Synapse persisted in firmware.
    // After this runs, Fn+Left emits a normal Left arrow scancode, our
    // RawInput-based fn_host_remap catches it, and the Fn+Left → Home
    // user binding starts working. Idempotent.
    //
    // Sequence (defensive): re-set MM mode → cmd=0xa4 unlock →
    // 4× cmd=0x0d passthrough writes → re-set MM mode (recovery).
    // The trailing set_device_mode is critical: cmd=0xa4 has previously
    // disturbed firmware composition state; re-issuing MM mode restores it.
    // Diagnostic: read current firmware Fn-layer Hypershift mappings.
    if args.len() >= 2 && args[1] == "read-hypershift" {
        logfile::init();
        eprintln!("=== read-hypershift CLI invocation ===");
        let dev = match usb_dongle::RazerDongle::open() {
            Some(d) => d,
            None => { eprintln!("read-hypershift: no dongle"); return; }
        };
        let arrows: &[(&str, u8)] = &[
            ("Left",  0x4F),
            ("Right", 0x59),
            ("Up",    0x53),
            ("Down",  0x54),
        ];
        for (name, matrix) in arrows {
            match dev.read_keymap_entry(*matrix, 0x01) {
                Ok(data) => {
                    let hex: String = data.iter().map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
                    eprintln!("read-hypershift: Fn+{} (matrix=0x{:02x}) -> [{}]", name, matrix, hex);
                }
                Err(e) => eprintln!("read-hypershift: Fn+{} err: {e}", name),
            }
        }
        return;
    }

    // PHASE 3 RISK GATE: re-flash captured STOCK firmware (wired USB).
    // 3-phase hidapi DFU (00:04→0x02CD/if3, download→0x110E/if0,
    // 00:87/0b→0x02CD/if3). Modes:
    //   joro-daemon fw-flash-stock           -> DRY (no USB I/O)
    //   joro-daemon fw-flash-stock --probe   -> phases A + open bootloader +
    //        status frames, STOP before 10:01 erase. ZERO brick risk
    //        (interrupted-before-erase auto-recovers). Validates transport.
    //   joro-daemon fw-flash-stock --commit  -> FULL FLASH
    if args.len() >= 2 && args[1] == "fw-flash-stock" {
        logfile::init();
        let mode = if args.iter().any(|a| a == "--commit-mod") {
            fwupdate::Mode::CommitMod
        } else if args.iter().any(|a| a == "--commit") {
            fwupdate::Mode::Commit
        } else if args.iter().any(|a| a == "--probe") {
            fwupdate::Mode::Probe
        } else {
            fwupdate::Mode::Dry
        };
        eprintln!("=== fw-flash-stock CLI ===");
        match fwupdate::flash_stock(mode) {
            Ok(()) => eprintln!("fw-flash-stock: OK"),
            Err(e) => eprintln!("fw-flash-stock: ERROR — {e}"),
        }
        return;
    }

    // Phase 1 debug: isolate whether cmd=0x0d lands when the documented
    // read-before-write precondition is satisfied, all in ONE session.
    // Prints the firmware entry bytes PRE / after-precondition-read /
    // POST-write / POST-transaction so we can see exactly where the
    // write is (or isn't) taking.
    if args.len() >= 2 && args[1] == "probe-write" {
        logfile::init();
        eprintln!("=== probe-write CLI invocation ===");
        let mut dev = match usb_dongle::RazerDongle::open() {
            Some(d) => d,
            None => { eprintln!("probe-write: no dongle"); return; }
        };
        let rd = |d: &usb_dongle::RazerDongle, tag: &str| {
            match d.read_keymap_entry(0x4F, 0x01) {
                Ok(b) => eprintln!(
                    "probe-write: {tag} Fn+Left = [{}]",
                    b.iter().map(|x| format!("{x:02x}")).collect::<Vec<_>>().join(" ")
                ),
                Err(e) => eprintln!("probe-write: {tag} read err: {e}"),
            }
        };
        let _ = dev.set_device_mode(false);
        rd(&dev, "PRE          ");
        if let Err(e) = dev.unlock_keymap_writes() { eprintln!("probe-write: unlock err {e}"); }
        rd(&dev, "after unlock ");      // this read is also the precondition
        match dev.set_layer_remap(0x4F, 0, 0x1D) {
            Ok(()) => eprintln!("probe-write: set_layer_remap(0x4F,0,0x1D) ok"),
            Err(e) => eprintln!("probe-write: set_layer_remap err: {e}"),
        }
        rd(&dev, "POST-write   ");
        if let Err(e) = dev.keymap_transaction() { eprintln!("probe-write: txn err {e}"); }
        rd(&dev, "POST-txn     ");
        let _ = dev.set_device_mode(false);
        eprintln!("probe-write: done. POST-write != PRE => cmd=0x0d lands with precondition.");
        return;
    }

    // Diagnostic: write Fn+Left -> 'z'. If keyboard then types 'z' on Fn+Left,
    // cmd=0x0d writes ARE landing through dongle. If still 'a', writes are silent.
    if args.len() >= 2 && args[1] == "test-hypershift-write" {
        logfile::init();
        eprintln!("=== test-hypershift-write CLI invocation ===");
        let mut dev = match usb_dongle::RazerDongle::open() {
            Some(d) => d,
            None => { eprintln!("test-hypershift-write: no dongle"); return; }
        };
        let _ = dev.set_device_mode(false);
        eprintln!("test: set MM mode pre");
        let _ = dev.unlock_keymap_writes();
        eprintln!("test: unlock_keymap_writes ok");
        // Fn+Left (matrix 0x4F) -> 'z' (HID usage 0x1D)
        match dev.set_layer_remap(0x4F, 0, 0x1D) {
            Ok(()) => eprintln!("test: Fn+Left -> 'z' (matrix=0x4f hid=0x1d) ok"),
            Err(e) => eprintln!("test: Fn+Left -> 'z' err: {e}"),
        }
        let _ = dev.set_device_mode(false);
        eprintln!("test: re-set MM mode post");
        eprintln!("test-hypershift-write: done. Press Fn+Left — if you see 'z' the write worked.");
        return;
    }

    // Phase 1 live test: write ONE distinctive Hypershift remap
    // (Fn+Left -> 'z'), then replay Synapse's class-0x0F VARSTORE commit
    // to flush it to flash. Operator then POWER-CYCLES the keyboard and
    // re-tests Fn+Left: if it still types 'z' after a cold boot,
    // persistence works.
    if args.len() >= 2 && args[1] == "persist-hypershift" {
        logfile::init();
        eprintln!("=== persist-hypershift CLI invocation ===");
        let mut dev = match usb_dongle::RazerDongle::open() {
            Some(d) => d,
            None => {
                eprintln!("persist-hypershift: no dongle");
                return;
            }
        };
        let _ = dev.set_device_mode(false);
        if let Err(e) = dev.unlock_keymap_writes() {
            eprintln!("persist-hypershift: unlock failed (continuing): {e}");
        }
        // Fn+Left (matrix 0x4F) -> 'z' (HID usage 0x1D) — distinctive
        // marker so a power-cycle survival check is unambiguous.
        let bindings = [(0x4Fu8, 0u8, 0x1Du8)];
        match dev.set_layer_remap(0x4F, 0, 0x1D) {
            Ok(()) => eprintln!("persist-hypershift: RAM write Fn+Left -> 'z' ok"),
            Err(e) => eprintln!("persist-hypershift: RAM write err: {e}"),
        }
        match dev.persist_keymap(&bindings) {
            Ok(()) => eprintln!("persist-hypershift: VARSTORE commit replay OK"),
            Err(e) => eprintln!("persist-hypershift: commit replay err: {e}"),
        }
        let _ = dev.set_device_mode(false);
        eprintln!(
            "persist-hypershift: DONE.\n  1. Confirm Fn+Left types 'z' NOW.\n  \
             2. FULLY power-cycle the keyboard (off, wait 5s, on).\n  \
             3. Re-test Fn+Left. Still 'z' = PERSISTED. Back to Left-arrow = RAM-only."
        );
        return;
    }

    if args.len() >= 2 && args[1] == "clear-hypershift" {
        // Init logfile so eprintln! output goes to daemon.log (release builds
        // are windows-subsystem; stderr goes nowhere otherwise).
        logfile::init();
        eprintln!("=== clear-hypershift CLI invocation ===");
        let mut dev = match usb_dongle::RazerDongle::open() {
            Some(d) => d,
            None => {
                eprintln!("clear-hypershift: no Joro dongle found");
                return;
            }
        };

        if let Err(e) = dev.set_device_mode(false) {
            eprintln!("clear-hypershift: set_device_mode(MM) pre: {e}");
        } else {
            eprintln!("clear-hypershift: set MM mode (pre)");
        }

        if let Err(e) = dev.unlock_keymap_writes() {
            eprintln!("clear-hypershift: unlock_keymap_writes: {e}");
        } else {
            eprintln!("clear-hypershift: unlock_keymap_writes ok");
        }
        // Synapse's "begin transaction" wrapper before any writes
        if let Err(e) = dev.keymap_transaction() {
            eprintln!("clear-hypershift: keymap_transaction(begin): {e}");
        } else {
            eprintln!("clear-hypershift: keymap_transaction(begin) ok");
        }

        // Passthrough Hypershift entries: Fn+Arrow emits the same arrow.
        // (matrix_index, hid_usage) per src/keys.rs JORO_MATRIX_TABLE +
        // standard HID Keyboard usage codes.
        let arrows: &[(&str, u8, u8)] = &[
            ("Left",  0x4F, 0x50),
            ("Right", 0x59, 0x4F),
            ("Up",    0x53, 0x52),
            ("Down",  0x54, 0x51),
        ];

        // Per Synapse USBPcap (2026-04-27): firmware silently drops cmd=0x0d
        // writes unless the target entry is READ via cmd=0x8d first. Synapse
        // reads ALL keys before writing; we just read the 4 arrows.
        for (name, matrix, _) in arrows {
            match dev.read_keymap_entry(*matrix, 0x01) {
                Ok(data) => {
                    let hex: String = data.iter().map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
                    eprintln!("clear-hypershift: read Fn+{} (matrix=0x{:02x}) -> [{}]", name, matrix, hex);
                }
                Err(e) => eprintln!("clear-hypershift: read Fn+{} err: {e}", name),
            }
        }

        for (name, matrix, hid_usage) in arrows {
            match dev.set_layer_remap(*matrix, 0, *hid_usage) {
                Ok(()) => eprintln!(
                    "clear-hypershift: Fn+{} -> passthrough (matrix=0x{:02x} hid=0x{:02x})",
                    name, matrix, hid_usage
                ),
                Err(e) => eprintln!("clear-hypershift: Fn+{}: {e}", name),
            }
        }

        // Synapse's "commit transaction" wrapper after the writes — required
        // to flush them to flash and exit edit mode (which restores Lock/
        // Copilot Win+L composition).
        if let Err(e) = dev.keymap_transaction() {
            eprintln!("clear-hypershift: keymap_transaction(commit): {e}");
        } else {
            eprintln!("clear-hypershift: keymap_transaction(commit) ok");
        }
        // Mode toggle as belt-and-suspenders re-init of composition state.
        if let Err(e) = dev.set_device_mode(true) {
            eprintln!("clear-hypershift: set_device_mode(Fn): {e}");
        }
        std::thread::sleep(std::time::Duration::from_millis(150));
        if let Err(e) = dev.set_device_mode(false) {
            eprintln!("clear-hypershift: set_device_mode(MM): {e}");
        } else {
            eprintln!("clear-hypershift: set MM mode");
        }

        eprintln!("clear-hypershift: done. Test Lock+Copilot still work, then Fn+Left should now produce Home (via daemon's fn_host_remap).");
        return;
    }

    // Diagnostic subcommands for keymap reverse engineering. See
    // project_hypershift_commit_trigger memory for current state.
    if args.len() >= 2 && args[1] == "diag-readlayers" {
        let dev = usb::RazerDevice::open().expect("no USB Joro");
        let matrix: u8 = args.get(2).and_then(|s| u8::from_str_radix(s.trim_start_matches("0x"), 16).ok()).unwrap_or(0x4f);
        println!("diag-readlayers: matrix=0x{matrix:02x}");
        for layer in &[0u8, 1, 2, 3] {
            let rargs = [0x01u8, matrix, *layer, 0, 0, 0, 0, 0, 0, 0];
            let rpkt = usb::build_packet(0x02, 0x8D, 10, &rargs);
            if let Ok(r) = dev.send_receive(&rpkt) {
                let p = usb::parse_packet(&r);
                let hex: String = p.args.iter().take(10).map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
                println!("  layer={layer}: status=0x{:02x} args={hex}", p.status);
            }
        }
        return;
    }

    // Surface any panic (including from background WebView2 / wry threads)
    // to stderr so daemon crashes are debuggable.
    let default_hook = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        eprintln!("joro-daemon PANIC: {info}");
        default_hook(info);
    }));

    eprintln!("joro-daemon starting...");

    let event_loop = EventLoop::<UserEvent>::with_user_event()
        .build()
        .expect("Failed to create event loop");
    let proxy = event_loop.create_proxy();
    // Expose the proxy to background threads (LL hook) for cross-thread
    // action dispatch, e.g. the keyboard-backlight special action.
    let _ = GLOBAL_PROXY.set(proxy.clone());

    // Register Ctrl+C handler so `cargo run` sessions can be stopped from the
    // terminal without skipping Drop. Without this, killing the daemon leaks
    // the WinRT GATT session and forces a re-pair in Windows.
    {
        let proxy_for_ctrlc = proxy.clone();
        if let Err(e) = ctrlc::set_handler(move || {
            let _ = proxy_for_ctrlc.send_event(UserEvent::CtrlC);
        }) {
            eprintln!("Warning: failed to install Ctrl+C handler: {e}");
        }
    }

    let mut app = App::new(proxy);
    event_loop.run_app(&mut app).expect("Event loop failed");
}
