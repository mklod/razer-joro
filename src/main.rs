// src/main.rs — Joro daemon main event loop
// Last modified: 2026-04-12

mod ble;
mod config;
mod consumer_hook;
mod device;
mod fn_detect;
mod keys;
mod remap;
mod settings_window;
mod tray;
mod usb;
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
}

use std::time::{Duration, Instant};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop, EventLoopProxy};
use winit::window::WindowId;

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
    last_battery_poll: Option<Instant>,
    cached_battery: Option<u8>,
    _window: Option<winit::window::Window>, // hidden window to keep event loop alive
    proxy: EventLoopProxy<UserEvent>,
    settings: Option<settings_window::SettingsWindow>,
    consumer_hook: Option<consumer_hook::ConsumerHook>,
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
            last_battery_poll: None,
            cached_battery: None,
            _window: None,
            proxy,
            settings: None,
            consumer_hook: None,
        }
    }

    /// Try to open the device; on success, apply config and update tray.
    /// Tries USB first, then falls back to BLE.
    fn try_connect(&mut self) {
        if self.device.is_some() {
            return;
        }

        let mut dev: Box<dyn JoroDevice> = if let Some(d) = usb::RazerDevice::open() {
            Box::new(d)
        } else if let Some(d) = ble::BleDevice::open() {
            Box::new(d)
        } else {
            return;
        };

        eprintln!("joro-daemon: {} device connected", dev.transport_name());
        // Re-run fn_detect enumeration. Idempotent — new HID collections
        // introduced by this transport (e.g. first BLE connect after
        // daemon boot) get readers spawned here.
        fn_detect::start();
        Self::apply_config(&self.config, &mut *dev);
        let fw = dev.get_firmware().ok();
        let transport = dev.transport_name();
        eprintln!("joro-daemon: {} firmware={:?}", transport, fw);
        // Read battery immediately on connect and cache it
        let battery = dev.get_battery_percent().ok();
        eprintln!("joro-daemon: {} battery={:?}%", transport, battery);

        // Apply Fn-layer remaps from config (USB-only — class 0x02 isn't
        // available over BLE). These persist in keyboard firmware so they
        // survive reboots and are active on any transport afterward.
        // Idempotent: re-applying is safe.
        if transport == "USB" {
            Self::apply_fn_remaps(&self.config, &mut *dev);
        }
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
        self.last_battery_poll = Some(Instant::now());
        // Clear the reconnect backoff so a future disconnect retries quickly
        self.last_reconnect_attempt = None;
        // Start the Consumer HID interception thread. hidapi opens a
        // separate handle (non-exclusive) so it coexists with the rusb
        // control-transfer handle we use for Protocol30 commands.
        if self.consumer_hook.is_none() {
            self.consumer_hook = consumer_hook::ConsumerHook::start(&self.config.consumer_remap);
        }
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
        for entry in &cfg.fn_remap {
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
                Ok(()) => eprintln!(
                    "joro-daemon: fn-layer {from} → {to} (matrix=0x{src_matrix:02x} mod=0x{modifier_byte:02x} usage=0x{dst_usage:02x})"
                ),
                Err(e) => eprintln!("Warning: fn_remap {from} → {to} failed: {e}"),
            }
        }
    }

    /// Apply the current config to a connected device. Static method so it can
    /// be called with `&self.config` and `&mut *self.device` without borrow conflicts.
    fn apply_config(cfg: &config::Config, dev: &mut dyn JoroDevice) {
        let rgb = cfg.lighting.parse_color().ok();
        match cfg.lighting.mode.as_str() {
            "breathing" => {
                if let Some((r, g, b)) = rgb {
                    if let Err(e) = dev.set_effect_breathing(r, g, b) {
                        eprintln!("Warning: set_effect_breathing failed: {e}");
                    }
                }
            }
            "spectrum" => {
                if let Err(e) = dev.set_effect_spectrum() {
                    eprintln!("Warning: set_effect_spectrum failed: {e}");
                }
            }
            _ => {
                // "static" or unknown — fall back to static color
                if let Some((r, g, b)) = rgb {
                    if let Err(e) = dev.set_static_color(r, g, b) {
                        eprintln!("Warning: set_static_color failed: {e}");
                    }
                }
            }
        }

        if let Err(e) = dev.set_brightness(cfg.lighting.brightness) {
            eprintln!("Warning: set_brightness failed: {e}");
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
    fn check_device(&mut self) {
        if let Some(ref mut dev) = self.device {
            if !dev.is_connected() {
                eprintln!("joro-daemon: {} device disconnected", dev.transport_name());
                self.device = None;
                self.cached_battery = None;
                // Stop the consumer hook — it'll be restarted on reconnect
                self.consumer_hook = None;
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

        // Not connected — rate-limit reconnect attempts. The BLE scan is
        // synchronous and blocks the main event loop; if we fire it every 2s
        // while disconnected, the tray menu becomes unresponsive. When USB
        // isn't present and BLE isn't advertising, back off to once every 10s.
        const RECONNECT_INTERVAL: Duration = Duration::from_secs(10);
        let now = Instant::now();
        if let Some(last) = self.last_reconnect_attempt {
            if now.duration_since(last) < RECONNECT_INTERVAL {
                return;
            }
        }
        self.last_reconnect_attempt = Some(now);
        self.try_connect();
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
        let (combo_table, trigger_table) = remap::build_remap_tables(&self.config.remap);
        let fn_host_table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);
        remap::update_fn_host_remap_table(fn_host_table);

        // Reapply to device if connected
        if let Some(ref mut dev) = self.device {
            Self::apply_config(&self.config, &mut **dev);
        }

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

    /// Periodic battery poll — called from about_to_wait every ~30s.
    /// Re-reads battery from the device, updates the cache, and pushes the
    /// new value to the settings webview if it's open.
    fn poll_battery(&mut self) {
        let Some(ref mut dev) = self.device else { return };
        match dev.get_battery_percent() {
            Ok(pct) => {
                let changed = self.cached_battery != Some(pct);
                self.cached_battery = Some(pct);
                if changed {
                    eprintln!("joro-daemon: battery {}%", pct);
                    self.push_battery_update();
                }
            }
            Err(_) => {
                // Battery read failed — don't zero the cache, just leave stale
            }
        }
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

                // Save to disk (preserves header + [lighting])
                if let Err(e) = config::save_remaps(&self.config_path, &self.config.remap) {
                    self.push_save_result(false, Some(&e));
                    return;
                }

                // Rebuild host-side remap tables
                let (combo_table, trigger_table) =
                    remap::build_remap_tables(&self.config.remap);
                let fn_host_table =
                    remap::build_fn_host_remap_table(&self.config.fn_host_remap);
                remap::update_remap_table(combo_table);
                remap::update_trigger_table(trigger_table);
                remap::update_fn_host_remap_table(fn_host_table);

                // Reapply to device (firmware keymap entries, if any)
                if let Some(ref mut dev) = self.device {
                    Self::apply_config(&self.config, &mut **dev);
                }

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

        // Apply to device — apply_config handles mode branching.
        if let Some(ref mut dev) = self.device {
            Self::apply_config(&self.config, &mut **dev);
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
        let (combo_table, trigger_table) = remap::build_remap_tables(&self.config.remap);
        let fn_host_table = remap::build_fn_host_remap_table(&self.config.fn_host_remap);
        eprintln!(
            "joro-daemon: {} combo remaps, {} trigger remaps, {} fn-host remaps",
            combo_table.len(),
            trigger_table.len(),
            fn_host_table.len()
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
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);
        remap::update_fn_host_remap_table(fn_host_table);
        remap::set_debug_log(true);

        // Fn-state HID reader. Enumerates Joro HID collections, opens the
        // vendor collection (usage 0x0001/0x0000), watches for Fn press /
        // release events (report `05 04 01` / `05 04 00`, verified over BLE
        // 2026-04-14), and updates `fn_detect::FN_HELD` for the hook.
        // Idempotent — safe to call again on device connect to pick up
        // collections that become visible after a transport change.
        fn_detect::start();

        // Try initial device connection
        self.try_connect();
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
        }
    }

    fn about_to_wait(&mut self, event_loop: &ActiveEventLoop) {
        let now = Instant::now();

        // Poll device every 2 seconds
        if now.duration_since(self.last_device_poll) >= Duration::from_secs(2) {
            self.last_device_poll = now;
            self.check_device();
        }

        // Poll config file every 5 seconds
        if now.duration_since(self.last_config_poll) >= Duration::from_secs(5) {
            self.last_config_poll = now;
            self.check_config_changed();
        }

        // Poll battery every 10 seconds when connected (catches transport
        // switches and reflects charge state changes fairly quickly).
        if self.device.is_some() {
            let should_poll = match self.last_battery_poll {
                Some(last) => now.duration_since(last) >= Duration::from_secs(10),
                None => true,
            };
            if should_poll {
                self.last_battery_poll = Some(now);
                self.poll_battery();
            }
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
    // CLI dispatch — recognise `scan <batch>` before constructing the event loop.
    let args: Vec<String> = std::env::args().collect();
    if args.len() >= 2 && args[1] == "scan" {
        let batch: u8 = args.get(2).and_then(|s| s.parse().ok()).unwrap_or(0);
        run_matrix_scan(batch);
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
