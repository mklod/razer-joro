// src/main.rs — Joro daemon main event loop
// Last modified: 2026-04-10--0045

mod config;
mod keys;
mod remap;
mod tray;
mod usb;

use std::time::{Duration, Instant};
use winit::application::ApplicationHandler;
use winit::event::WindowEvent;
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::window::WindowId;

// ── App state ─────────────────────────────────────────────────────────────────

struct App {
    tray: Option<tray::JoroTray>,
    device: Option<usb::RazerDevice>,
    config: config::Config,
    config_path: std::path::PathBuf,
    config_modified: Option<std::time::SystemTime>,
    last_device_poll: Instant,
    last_config_poll: Instant,
    _window: Option<winit::window::Window>, // hidden window to keep event loop alive
}

impl App {
    fn new() -> Self {
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
            _window: None,
        }
    }

    /// Try to open the device; on success, apply config and update tray.
    fn try_connect(&mut self) {
        if self.device.is_some() {
            return;
        }
        if let Some(dev) = usb::RazerDevice::open() {
            eprintln!("joro-daemon: device connected");
            self.apply_config(&dev);
            let fw = dev.get_firmware().ok();
            if let Some(ref mut tray) = self.tray {
                tray.set_connected(true, fw.as_deref());
            }
            self.device = Some(dev);
        }
    }

    /// Apply the current config to a device.
    fn apply_config(&self, dev: &usb::RazerDevice) {
        // Set color
        if let Ok((r, g, b)) = self.config.lighting.parse_color() {
            if let Err(e) = dev.set_static_color(r, g, b) {
                eprintln!("Warning: set_static_color failed: {e}");
            }
        }

        // Set brightness
        if let Err(e) = dev.set_brightness(self.config.lighting.brightness) {
            eprintln!("Warning: set_brightness failed: {e}");
        }

        // Apply firmware keymap entries (single-key remaps only; combos handled by host hook)
        for remap in &self.config.remap {
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
        if let Some(ref dev) = self.device {
            if !dev.is_connected() {
                eprintln!("joro-daemon: device disconnected");
                self.device = None;
                if let Some(ref mut tray) = self.tray {
                    tray.set_connected(false, None);
                }
            }
        } else {
            self.try_connect();
        }
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
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);

        // Reapply to device
        if let Some(ref dev) = self.device {
            self.apply_config(dev);
        }
    }

    /// Handle tray menu events.
    fn handle_menu_events(&mut self, event_loop: &ActiveEventLoop) {
        while let Some(event) = tray::poll_menu_event() {
            let id = &event.id;
            if let Some(ref tray) = self.tray {
                if id == &tray.menu_quit_id {
                    eprintln!("joro-daemon: quit requested");
                    remap::remove_hook();
                    event_loop.exit();
                } else if id == &tray.menu_reload_id {
                    eprintln!("joro-daemon: manual config reload");
                    self.reload_config();
                } else if id == &tray.menu_open_config_id {
                    let path = self.config_path.to_string_lossy().to_string();
                    let _ = std::process::Command::new("cmd")
                        .args(["/C", "start", "", &path])
                        .spawn();
                }
            }
        }
    }
}

// ── ApplicationHandler ────────────────────────────────────────────────────────

impl ApplicationHandler for App {
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
        eprintln!("joro-daemon: {} combo remaps, {} trigger remaps", combo_table.len(), trigger_table.len());
        for t in &trigger_table {
            eprintln!("  trigger: gate=0x{:04X} trigger=0x{:04X} prefix={:?} -> mods={:?} key=0x{:04X}",
                t.gate_mod_vk, t.trigger_vk, t.prefix_mods, t.output_mods, t.output_key);
        }
        remap::update_remap_table(combo_table);
        remap::update_trigger_table(trigger_table);
        // Debug logging disabled — enable for targeted debugging only.
        // Logs to %LOCALAPPDATA%\razer-joro-target\hook_debug.log
        // remap::set_debug_log(true);

        // Try initial device connection
        self.try_connect();
    }

    fn window_event(
        &mut self,
        _event_loop: &ActiveEventLoop,
        _window_id: WindowId,
        _event: WindowEvent,
    ) {
        // No windows; nothing to do
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

        // Handle menu events
        self.handle_menu_events(event_loop);

        // Wake up every 100ms to poll
        event_loop.set_control_flow(ControlFlow::WaitUntil(
            Instant::now() + Duration::from_millis(100),
        ));
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

fn main() {
    eprintln!("joro-daemon starting...");
    let event_loop = EventLoop::new().expect("Failed to create event loop");
    let mut app = App::new();
    event_loop.run_app(&mut app).expect("Event loop failed");
}
