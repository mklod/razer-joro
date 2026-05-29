// src/device.rs — common transport trait for Razer Joro keyboard
// Last modified: 2026-04-12

/// Common interface for Joro keyboard transports (USB, BLE, future dongle).
///
/// All methods take `&mut self` so a single trait works for backends that
/// need interior state mutation (e.g. BLE transaction IDs).
///
/// `Send` supertrait: a freshly-opened device must be transferable from a
/// background reconnect thread back to the main event-loop thread. The
/// reconnect probe (dongle heartbeat ~2 s, BLE WinRT scan 5 s+) is run off
/// the main thread so the webview never freezes; the resulting boxed device
/// is moved to the main thread via a shared slot. All three backends are
/// Send (hidapi HidDevice, rusb DeviceHandle, agile WinRT GATT objects).
pub trait JoroDevice: Send {
    /// Check if the device is still reachable.
    fn is_connected(&mut self) -> bool;

    /// Read firmware version string (e.g. "v1.2.2.0").
    fn get_firmware(&mut self) -> Result<String, String>;

    /// Set static lighting color.
    fn set_static_color(&mut self, r: u8, g: u8, b: u8) -> Result<(), String>;

    /// Set lighting brightness (0-255).
    fn set_brightness(&mut self, level: u8) -> Result<(), String>;

    /// Set a breathing effect with a single color. Default: fall back to static.
    fn set_effect_breathing(&mut self, r: u8, g: u8, b: u8) -> Result<(), String> {
        self.set_static_color(r, g, b)
    }

    /// Set a full-keyboard spectrum cycling effect. Default: no-op.
    fn set_effect_spectrum(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Set a firmware keymap entry. BLE returns Ok(()) without action
    /// because Joro firmware ignores class 0x02 over BLE.
    fn set_keymap_entry(&mut self, index: u8, usage: u8) -> Result<(), String>;

    /// Read battery level (0-100). Default: unsupported.
    fn get_battery_percent(&mut self) -> Result<u8, String> {
        Err("battery not supported".into())
    }

    /// Write a base-layer firmware keymap entry via class=0x02 cmd=0x0d.
    /// Default: unsupported (BLE returns Err — Joro firmware ignores class
    /// 0x02 over BLE). `src_matrix` is the Razer matrix index, `modifier`
    /// is the HID modifier byte for combo outputs (0 = none), `dst_usage`
    /// is the HID keyboard usage code.
    ///
    /// Note: this targets the base keymap table and can only remap keys
    /// whose output routes through the matrix. F-row keys in mm-primary
    /// mode emit consumer usages from a separate firmware pipeline that
    /// bypasses the matrix — those need host-side interception instead.
    fn set_layer_remap(
        &mut self,
        _src_matrix: u8,
        _modifier: u8,
        _dst_usage: u8,
    ) -> Result<(), String> {
        Err("set_layer_remap requires USB transport".into())
    }

    /// Short label for logging ("USB" / "BLE").
    fn transport_name(&self) -> &'static str;

    /// Set Joro's firmware-level device mode. `fn_primary = true` puts the
    /// keyboard in driver mode — F4-F12 emit plain VK_F4..VK_F12 scancodes
    /// the host LL hook can intercept and rewrite. `false` is MM mode where
    /// F5-F9 emit consumer usages (mute/vol/brightness).
    ///
    /// BLE: Protocol30 `SET class=0x01 cmd=0x02 sub=00,00 data=[mode, 0]`.
    /// USB: not yet implemented — default no-op.
    fn set_device_mode(&mut self, _fn_primary: bool) -> Result<(), String> {
        Ok(())
    }

    /// Begin keymap edit session. The dongle requires a `class=0x02 cmd=0xa4`
    /// "unlock" call before it accepts any `cmd=0x0d` Hypershift write —
    /// otherwise writes are silently dropped. Direct USB and BLE backends
    /// don't need this (default no-op).
    fn unlock_keymap_writes(&mut self) -> Result<(), String> {
        Ok(())
    }

    /// Disable firmware idle sleep. `seconds = 0` = never sleep (openrazer
    /// convention). Useful for keep-alive when responsiveness matters more
    /// than battery life. Default no-op for backends that haven't implemented
    /// the command yet.
    fn set_idle_time(&mut self, _seconds: u16) -> Result<(), String> {
        Ok(())
    }

    /// Persist the live (RAM) Hypershift keymap to the keyboard's flash
    /// config store so remaps survive a power cycle.
    ///
    /// `bindings` are the (matrix, modifier, dst_usage) triples the caller
    /// just wrote via `set_layer_remap`. The dongle backend replays
    /// Synapse's proven class-0x0F VARSTORE commit transaction (captured,
    /// embedded) substituting these bindings; every other byte is
    /// byte-identical to a known-good Synapse save. Other transports:
    /// default no-op (BLE never persists; direct-USB uses the transport
    /// cycle documented in `project_hypershift_commit_trigger.md`).
    fn persist_keymap(&mut self, _bindings: &[(u8, u8, u8)]) -> Result<(), String> {
        Ok(())
    }
}
