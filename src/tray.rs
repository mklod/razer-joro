// src/tray.rs — Systray icon and menu
// Last modified: 2026-04-09--2300

use tray_icon::{
    menu::{Menu, MenuEvent, MenuItem, PredefinedMenuItem},
    Icon, TrayIcon, TrayIconBuilder,
};

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
    pub menu_quit_id: tray_icon::menu::MenuId,
    status_item: MenuItem,
    firmware_item: MenuItem,
}

impl JoroTray {
    pub fn new() -> Self {
        let status_item = MenuItem::new("Razer Joro \u{2014} Disconnected", false, None);
        let firmware_item = MenuItem::new("Firmware: \u{2014}", false, None);
        let reload_item = MenuItem::new("Reload Config", true, None);
        let open_config_item = MenuItem::new("Open Config File", true, None);
        let quit_item = MenuItem::new("Quit", true, None);

        let menu_reload_id = reload_item.id().clone();
        let menu_open_config_id = open_config_item.id().clone();
        let menu_quit_id = quit_item.id().clone();

        let menu = Menu::with_items(&[
            &status_item,
            &firmware_item,
            &PredefinedMenuItem::separator(),
            &reload_item,
            &open_config_item,
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
            menu_quit_id,
            status_item,
            firmware_item,
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
