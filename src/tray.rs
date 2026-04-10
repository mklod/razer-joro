// src/tray.rs — Systray icon and menu
// Last modified: 2026-04-10--0200

use tray_icon::{
    menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem},
    Icon, TrayIcon, TrayIconBuilder,
};
use windows::Win32::System::Registry::{
    RegCreateKeyExW, RegDeleteValueW, RegQueryValueExW, RegSetValueExW,
    HKEY_CURRENT_USER, KEY_READ, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ,
};
use windows::core::PCWSTR;

// ── Icon generation ───────────────────────────────────────────────────────────

/// Generate a 32x32 RGBA icon — green circle if connected, grey if not.
pub fn create_icon(connected: bool) -> Icon {
    let (cr, cg, cb) = if connected {
        (0x00u8, 0xCCu8, 0x44u8) // green
    } else {
        (0x88u8, 0x88u8, 0x88u8) // grey
    };

    const SIZE: usize = 32;
    const RADIUS_SQ: i32 = 14 * 14;
    const CENTER: i32 = 16;

    let mut rgba = vec![0u8; SIZE * SIZE * 4];
    for y in 0..SIZE as i32 {
        for x in 0..SIZE as i32 {
            let dx = x - CENTER;
            let dy = y - CENTER;
            let idx = ((y as usize) * SIZE + x as usize) * 4;
            if dx * dx + dy * dy <= RADIUS_SQ {
                rgba[idx] = cr;
                rgba[idx + 1] = cg;
                rgba[idx + 2] = cb;
                rgba[idx + 3] = 0xFF; // opaque
            } else {
                // transparent
                rgba[idx] = 0;
                rgba[idx + 1] = 0;
                rgba[idx + 2] = 0;
                rgba[idx + 3] = 0;
            }
        }
    }

    Icon::from_rgba(rgba, SIZE as u32, SIZE as u32)
        .expect("Failed to create tray icon from RGBA data")
}

// ── JoroTray ──────────────────────────────────────────────────────────────────

pub struct JoroTray {
    _tray: TrayIcon,
    pub menu_reload_id: tray_icon::menu::MenuId,
    pub menu_open_config_id: tray_icon::menu::MenuId,
    pub menu_autostart_id: tray_icon::menu::MenuId,
    pub menu_quit_id: tray_icon::menu::MenuId,
    status_item: MenuItem,
    firmware_item: MenuItem,
    autostart_item: MenuItem,
}

impl JoroTray {
    pub fn new() -> Self {
        let status_item = MenuItem::new("Razer Joro \u{2014} Disconnected", false, None);
        let firmware_item = MenuItem::new("Firmware: \u{2014}", false, None);
        let reload_item = MenuItem::new("Reload Config", true, None);
        let open_config_item = MenuItem::new("Open Config File", true, None);
        let autostart_label = if is_autostart_enabled() {
            "Autostart: On"
        } else {
            "Autostart: Off"
        };
        let autostart_item = MenuItem::new(autostart_label, true, None);
        let quit_item = MenuItem::new("Quit", true, None);

        let menu_reload_id = reload_item.id().clone();
        let menu_open_config_id = open_config_item.id().clone();
        let menu_autostart_id = autostart_item.id().clone();
        let menu_quit_id = quit_item.id().clone();

        let menu = Menu::with_items(&[
            &status_item,
            &firmware_item,
            &PredefinedMenuItem::separator(),
            &reload_item,
            &open_config_item,
            &autostart_item,
            &PredefinedMenuItem::separator(),
            &quit_item,
        ])
        .expect("Failed to create tray menu");

        let icon = create_icon(false);

        let tray = TrayIconBuilder::new()
            .with_tooltip("Joro Daemon")
            .with_icon(icon)
            .with_menu(Box::new(menu))
            .build()
            .expect("Failed to create tray icon");

        JoroTray {
            _tray: tray,
            menu_reload_id,
            menu_open_config_id,
            menu_autostart_id,
            menu_quit_id,
            status_item,
            firmware_item,
            autostart_item,
        }
    }

    /// Toggle autostart and update the menu label.
    pub fn toggle_autostart(&self) {
        if is_autostart_enabled() {
            disable_autostart();
            self.autostart_item.set_text("Autostart: Off");
            eprintln!("joro-daemon: autostart disabled");
        } else {
            enable_autostart();
            self.autostart_item.set_text("Autostart: On");
            eprintln!("joro-daemon: autostart enabled");
        }
    }

    /// Update the tray icon and menu status items based on connection state.
    pub fn set_connected(&mut self, connected: bool, firmware: Option<&str>) {
        let icon = create_icon(connected);
        let _ = self._tray.set_icon(Some(icon));

        let status_text = if connected {
            "Razer Joro \u{2014} Connected"
        } else {
            "Razer Joro \u{2014} Disconnected"
        };
        self.status_item.set_text(status_text);

        let fw_text = match firmware {
            Some(fw) => format!("Firmware: {}", fw),
            None => "Firmware: \u{2014}".to_string(),
        };
        self.firmware_item.set_text(fw_text);
    }
}

// ── Menu event polling ────────────────────────────────────────────────────────

/// Non-blocking poll for a menu event.
pub fn poll_menu_event() -> Option<MenuEvent> {
    MenuEvent::receiver().try_recv().ok()
}

// ── Autostart (registry) ─────────────────────────────────────────────────────

const RUN_KEY: &str = r"Software\Microsoft\Windows\CurrentVersion\Run";
const VALUE_NAME: &str = "JoroDaemon";

fn run_key_wide() -> Vec<u16> {
    RUN_KEY.encode_utf16().chain(std::iter::once(0)).collect()
}

fn value_name_wide() -> Vec<u16> {
    VALUE_NAME.encode_utf16().chain(std::iter::once(0)).collect()
}

/// Check if autostart registry value exists.
pub fn is_autostart_enabled() -> bool {
    let key_w = run_key_wide();
    let val_w = value_name_wide();
    unsafe {
        let mut hkey = std::mem::zeroed();
        let res = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(key_w.as_ptr()),
            0,
            None,
            REG_OPTION_NON_VOLATILE,
            KEY_READ,
            None,
            &mut hkey,
            None,
        );
        if res.is_err() {
            return false;
        }
        let exists = RegQueryValueExW(
            hkey,
            PCWSTR(val_w.as_ptr()),
            None,
            None,
            None,
            None,
        ).is_ok();
        let _ = windows::Win32::System::Registry::RegCloseKey(hkey);
        exists
    }
}

/// Set autostart registry value to current exe path.
pub fn enable_autostart() {
    let exe = std::env::current_exe().unwrap_or_default();
    let exe_str = exe.to_string_lossy();
    let exe_w: Vec<u16> = exe_str.encode_utf16().chain(std::iter::once(0)).collect();
    let key_w = run_key_wide();
    let val_w = value_name_wide();

    unsafe {
        let mut hkey = std::mem::zeroed();
        let res = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(key_w.as_ptr()),
            0,
            None,
            REG_OPTION_NON_VOLATILE,
            KEY_WRITE,
            None,
            &mut hkey,
            None,
        );
        if res.is_err() {
            eprintln!("Warning: failed to open Run registry key");
            return;
        }
        let byte_len = (exe_w.len() * 2) as u32;
        let _ = RegSetValueExW(
            hkey,
            PCWSTR(val_w.as_ptr()),
            0,
            REG_SZ,
            Some(std::slice::from_raw_parts(exe_w.as_ptr() as *const u8, byte_len as usize)),
        );
        let _ = windows::Win32::System::Registry::RegCloseKey(hkey);
    }
}

/// Remove autostart registry value.
pub fn disable_autostart() {
    let key_w = run_key_wide();
    let val_w = value_name_wide();

    unsafe {
        let mut hkey = std::mem::zeroed();
        let res = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            PCWSTR(key_w.as_ptr()),
            0,
            None,
            REG_OPTION_NON_VOLATILE,
            KEY_WRITE,
            None,
            &mut hkey,
            None,
        );
        if res.is_err() {
            return;
        }
        let _ = RegDeleteValueW(hkey, PCWSTR(val_w.as_ptr()));
        let _ = windows::Win32::System::Registry::RegCloseKey(hkey);
    }
}
