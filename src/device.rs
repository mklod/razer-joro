// src/device.rs — common transport trait for Razer Joro keyboard
// Last modified: 2026-04-12

/// Common interface for Joro keyboard transports (USB, BLE, future dongle).
///
/// All methods take `&mut self` so a single trait works for backends that
/// need interior state mutation (e.g. BLE transaction IDs).
pub trait JoroDevice {
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
}
