// src/window_state.rs — persist settings window position
// Last modified: 2026-04-12

use std::path::PathBuf;

const STATE_FILE: &str = "window_state.json";

/// Persisted window position. JSON format so it's easy to edit by hand.
/// Only the position is persisted — size is fixed by the daemon.
#[derive(Debug, Clone, Copy, serde::Serialize, serde::Deserialize)]
pub struct SettingsWindowState {
    pub x: i32,
    pub y: i32,
}

fn state_path() -> Option<PathBuf> {
    let appdata = std::env::var_os("APPDATA")?;
    Some(PathBuf::from(appdata).join("razer-joro").join(STATE_FILE))
}

pub fn load() -> Option<SettingsWindowState> {
    let path = state_path()?;
    let s = std::fs::read_to_string(&path).ok()?;
    serde_json::from_str(&s).ok()
}

pub fn save(state: SettingsWindowState) {
    let Some(path) = state_path() else { return };
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    match serde_json::to_string_pretty(&state) {
        Ok(s) => {
            if let Err(e) = std::fs::write(&path, s) {
                eprintln!("Warning: window_state save failed: {e}");
            }
        }
        Err(e) => eprintln!("Warning: window_state serialize failed: {e}"),
    }
}
