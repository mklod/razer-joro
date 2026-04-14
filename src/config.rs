// src/config.rs — TOML config schema and loader
// Last modified: 2026-04-09--2350

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct Config {
    pub lighting: LightingConfig,
    #[serde(default)]
    pub remap: Vec<RemapConfig>,
    /// Fn-layer (Razer "Hypershift") remaps. Programmed into keyboard firmware
    /// via class=0x02 cmd=0x0d (USB only — class 0x02 not supported over BLE).
    /// Persists in firmware across reboots and works on any transport.
    #[serde(default)]
    pub fn_remap: Vec<FnRemapConfig>,
    /// Host-side Fn-layer remaps. Applied by the daemon's WH_KEYBOARD_LL
    /// hook using live Fn-held state from `fn_detect` (vendor HID report
    /// 0x05 0x04 state). Unlike `fn_remap` these don't require USB and
    /// don't touch firmware — they work on any transport as long as the
    /// daemon is running. Same (from, to) schema as `fn_remap`.
    #[serde(default)]
    pub fn_host_remap: Vec<FnRemapConfig>,
    /// Host-side Consumer HID interceptions. Joro's F-row emits consumer
    /// usages in mm-primary mode (F5=Mute, F8=BrightnessDown, etc.). These
    /// entries let the daemon swallow a specific consumer usage and emit a
    /// replacement keyboard VK via SendInput — used to give e.g. F4 a
    /// keyboard-level meaning even though the firmware routes it through
    /// the consumer pipeline.
    #[serde(default)]
    pub consumer_remap: Vec<ConsumerRemapConfig>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct LightingConfig {
    pub mode: String,
    pub color: String,
    pub brightness: u8,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct RemapConfig {
    pub name: String,
    pub from: String,
    pub to: String,
    #[serde(skip_serializing_if = "Option::is_none", default)]
    pub matrix_index: Option<u8>,
}

/// Fn-layer remap entry. `from` is the source key name (e.g. "Left", "Right").
/// `to` is the output, either a single key ("Home") or a combo ("Ctrl+F12").
/// The remap is programmed into firmware via `set_fn_layer_remap()`.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct FnRemapConfig {
    #[serde(default)]
    pub name: String,
    pub from: String,
    pub to: String,
}

/// Host-side Consumer HID interception entry. `from` is a Joro consumer
/// usage name (e.g. "Mute" = 0x00E2) or a raw hex code like "0x00e2". `to`
/// is a single key name or combo that the daemon emits via SendInput when
/// it sees the source usage in a Consumer Control report.
#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ConsumerRemapConfig {
    #[serde(default)]
    pub name: String,
    pub from: String,
    pub to: String,
}

const DEFAULT_CONFIG: &str = r##"# Razer Joro Daemon Config

[lighting]
mode = "static"
color = "#FFFFFF"
brightness = 128

# Key remaps
# from = single key name for firmware/host remap
# from = "Modifier+Key" for combo-source intercept (e.g., keyboard sends Win+L)
# to = single key for simple output, "Modifier+Key" for combo output

[[remap]]
name = "Lock key to Delete"
from = "Win+L"
to = "Delete"

[[remap]]
name = "Copilot key to Ctrl+F12"
from = "Win+Copilot"
to = "Ctrl+F12"

# [[remap]]
# name = "CapsLock to Ctrl+F12"
# from = "CapsLock"
# to = "Ctrl+F12"

# Fn-layer remaps (Razer "Hypershift")
# These are programmed into the keyboard firmware via class=0x02 cmd=0x0d.
# USB only — class 0x02 is not available over BLE. Once written, the remap
# persists across reboots and works on any transport (USB/BLE/dongle).
# Only keys whose Joro matrix index we know can be Fn-remapped.

[[fn_remap]]
name = "Fn+Left to Home"
from = "Left"
to = "Home"

[[fn_remap]]
name = "Fn+Right to End"
from = "Right"
to = "End"
# matrix_index = 30
"##;

impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let contents = std::fs::read_to_string(path)
            .map_err(|e| format!("Failed to read config file {}: {}", path.display(), e))?;
        toml::from_str(&contents)
            .map_err(|e| format!("Failed to parse config file {}: {}", path.display(), e))
    }
}

/// Update a single field within the `[lighting]` section of the TOML file in-place.
/// Preserves all comments and other sections. `new_value` is the raw TOML value
/// (e.g. `"\"#FF0000\""` for a string, `"200"` for an integer).
pub fn save_lighting_field(path: &Path, key: &str, new_value: &str) -> Result<(), String> {
    let contents = std::fs::read_to_string(path)
        .map_err(|e| format!("read {}: {}", path.display(), e))?;

    let mut out = String::with_capacity(contents.len());
    let mut in_lighting = false;
    let mut updated = false;
    for line in contents.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with('[') {
            // New section — leave the lighting scope if we're entering a different one
            in_lighting = trimmed.starts_with("[lighting]");
        } else if in_lighting && !updated {
            // Match `<key> = ...` (ignoring leading whitespace)
            if let Some(eq_idx) = trimmed.find('=') {
                let lhs = trimmed[..eq_idx].trim();
                if lhs == key {
                    // Preserve original indentation
                    let indent_len = line.len() - trimmed.len();
                    let indent = &line[..indent_len];
                    out.push_str(&format!("{}{} = {}", indent, key, new_value));
                    out.push('\n');
                    updated = true;
                    continue;
                }
            }
        }
        out.push_str(line);
        out.push('\n');
    }

    if !updated {
        return Err(format!("lighting.{} not found in config", key));
    }

    // Preserve trailing newline status of original
    if !contents.ends_with('\n') && out.ends_with('\n') {
        out.pop();
    }

    std::fs::write(path, out)
        .map_err(|e| format!("write {}: {}", path.display(), e))
}

/// Re-serialize the entire Config struct to TOML and write it to disk.
/// Loses comments, but reliably produces a parseable file with all sections.
/// Use targeted helpers (`save_lighting_field`, `save_remaps`) when you want
/// to preserve user comments. Use this for whole-config writes from the UI.
pub fn save_config(path: &Path, cfg: &Config) -> Result<(), String> {
    let toml_str = toml::to_string_pretty(cfg)
        .map_err(|e| format!("serialize config: {e}"))?;
    std::fs::write(path, toml_str)
        .map_err(|e| format!("write {}: {}", path.display(), e))
}

/// Rewrite the `[[remap]]` section of the TOML file in place. Everything
/// before the first `[[remap]]` line is preserved verbatim (keeps the header
/// comments and the `[lighting]` section). The remap section is regenerated
/// from the given Vec.
pub fn save_remaps(path: &Path, remaps: &[RemapConfig]) -> Result<(), String> {
    let contents = std::fs::read_to_string(path)
        .map_err(|e| format!("read {}: {}", path.display(), e))?;

    // Find the first `[[remap]]` line (preserving everything before it)
    let mut header = String::new();
    let mut found_remap_start = false;
    for line in contents.lines() {
        let trimmed = line.trim_start();
        if trimmed.starts_with("[[remap]]") {
            found_remap_start = true;
            break;
        }
        header.push_str(line);
        header.push('\n');
    }

    // If no [[remap]] in the file, keep the entire original contents as header
    // (minus trailing newline, which we'll re-add).
    if !found_remap_start {
        header = contents.clone();
        if !header.ends_with('\n') {
            header.push('\n');
        }
    }

    // Build the new remap section via toml::to_string on a wrapper struct
    #[derive(Serialize)]
    struct RemapWrapper<'a> {
        remap: &'a [RemapConfig],
    }
    let wrapper = RemapWrapper { remap: remaps };
    let remap_toml = toml::to_string(&wrapper)
        .map_err(|e| format!("serialize remaps: {e}"))?;

    // Ensure a blank line between header and remaps if the header doesn't end with one
    let mut out = header;
    if !out.ends_with("\n\n") && !out.is_empty() {
        out.push('\n');
    }
    out.push_str(&remap_toml);

    std::fs::write(path, out)
        .map_err(|e| format!("write {}: {}", path.display(), e))
}

impl LightingConfig {
    pub fn parse_color(&self) -> Result<(u8, u8, u8), String> {
        let s = &self.color;
        if !s.starts_with('#') {
            return Err(format!("Color '{}' must start with '#'", s));
        }
        let hex = &s[1..];
        if hex.len() != 6 {
            return Err(format!("Color '{}' must be in #RRGGBB format", s));
        }
        let r = u8::from_str_radix(&hex[0..2], 16)
            .map_err(|_| format!("Invalid red component in '{}'", s))?;
        let g = u8::from_str_radix(&hex[2..4], 16)
            .map_err(|_| format!("Invalid green component in '{}'", s))?;
        let b = u8::from_str_radix(&hex[4..6], 16)
            .map_err(|_| format!("Invalid blue component in '{}'", s))?;
        Ok((r, g, b))
    }
}

pub fn config_path() -> PathBuf {
    let appdata = std::env::var("APPDATA").unwrap_or_else(|_| ".".to_string());
    PathBuf::from(appdata).join("razer-joro").join("config.toml")
}

pub fn ensure_config() -> Result<PathBuf, String> {
    let path = config_path();
    if !path.exists() {
        let dir = path.parent().ok_or("Config path has no parent directory")?;
        std::fs::create_dir_all(dir)
            .map_err(|e| format!("Failed to create config directory {}: {}", dir.display(), e))?;
        std::fs::write(&path, DEFAULT_CONFIG)
            .map_err(|e| format!("Failed to write default config to {}: {}", path.display(), e))?;
    }
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_parse_minimal_config() {
        let toml_str = r##"
[lighting]
mode = "static"
color = "#FF4400"
brightness = 200
"##;
        let config: Config = toml::from_str(toml_str).unwrap();
        assert_eq!(config.lighting.mode, "static");
        assert_eq!(config.lighting.brightness, 200);
        assert!(config.remap.is_empty());
    }

    #[test]
    fn test_parse_config_with_remaps() {
        let toml_str = r##"
[lighting]
mode = "static"
color = "#FF0000"
brightness = 255

[[remap]]
name = "CapsLock to Ctrl+F12"
from = "CapsLock"
to = "Ctrl+F12"
matrix_index = 30

[[remap]]
name = "Escape to Grave"
from = "Escape"
to = "Grave"
matrix_index = 1
"##;
        let config: Config = toml::from_str(toml_str).unwrap();
        assert_eq!(config.remap.len(), 2);
        assert_eq!(config.remap[0].from, "CapsLock");
        assert_eq!(config.remap[0].to, "Ctrl+F12");
        assert_eq!(config.remap[0].matrix_index, Some(30));
        assert_eq!(config.remap[1].matrix_index, Some(1));
    }

    #[test]
    fn test_parse_color_valid() {
        let lc = LightingConfig { mode: "static".into(), color: "#FF8800".into(), brightness: 100 };
        assert_eq!(lc.parse_color().unwrap(), (0xFF, 0x88, 0x00));
    }

    #[test]
    fn test_parse_color_black() {
        let lc = LightingConfig { mode: "static".into(), color: "#000000".into(), brightness: 0 };
        assert_eq!(lc.parse_color().unwrap(), (0, 0, 0));
    }

    #[test]
    fn test_parse_color_missing_hash() {
        let lc = LightingConfig { mode: "static".into(), color: "FF0000".into(), brightness: 0 };
        assert!(lc.parse_color().is_err());
    }

    #[test]
    fn test_default_config_parses() {
        let config: Config = toml::from_str(DEFAULT_CONFIG).unwrap();
        assert_eq!(config.lighting.color, "#FFFFFF");
        assert_eq!(config.lighting.brightness, 128);
        assert_eq!(config.remap.len(), 2);
        assert_eq!(config.remap[0].from, "Win+L");
        assert_eq!(config.remap[0].to, "Delete");
        assert_eq!(config.remap[1].from, "Win+Copilot");
        assert_eq!(config.remap[1].to, "Ctrl+F12");
    }
}
