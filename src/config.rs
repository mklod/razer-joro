// src/config.rs — TOML config schema and loader
// Last modified: 2026-04-09--2200

use serde::Deserialize;
use std::path::{Path, PathBuf};

#[derive(Debug, Deserialize, Clone)]
pub struct Config {
    pub lighting: LightingConfig,
    #[serde(default)]
    pub remap: Vec<RemapConfig>,
}

#[derive(Debug, Deserialize, Clone)]
pub struct LightingConfig {
    pub mode: String,
    pub color: String,
    pub brightness: u8,
}

#[derive(Debug, Deserialize, Clone)]
pub struct RemapConfig {
    pub name: String,
    pub from: String,
    pub to: String,
    pub matrix_index: Option<u8>,
}

const DEFAULT_CONFIG: &str = r##"# Razer Joro Daemon Config

[lighting]
mode = "static"
color = "#FFFFFF"
brightness = 128

# Uncomment and edit to add remaps:
# [[remap]]
# name = "CapsLock to Ctrl+F12"
# from = "CapsLock"
# to = "Ctrl+F12"
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
    }
}
