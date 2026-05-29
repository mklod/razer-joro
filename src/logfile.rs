// src/logfile.rs — redirect stderr/stdout to a log file in release builds
// Last modified: 2026-04-16--1920
//
// Release builds run as windows_subsystem = "windows": no console is
// attached, so eprintln!/println! writes vanish. This module opens a
// log file in %LOCALAPPDATA%\razer-joro\ and points the process stderr
// handle at it via SetStdHandle, so every existing eprintln! in the
// codebase lands in the file with no further code changes.
//
// Size guard: if the file is already over LOG_MAX_BYTES at startup,
// rotate once (rename to daemon.log.1, overwrite any older rotation).
// Keeps the live log readable without unbounded growth during long
// uptime sessions.

use std::os::windows::io::IntoRawHandle;
use std::path::PathBuf;

use windows::Win32::Foundation::HANDLE;
use windows::Win32::System::Console::{SetStdHandle, STD_ERROR_HANDLE, STD_OUTPUT_HANDLE};

const LOG_MAX_BYTES: u64 = 1_000_000; // ~1 MB, plenty for a few days of chatter

fn log_path() -> PathBuf {
    let base = std::env::var("LOCALAPPDATA")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from(r"C:\Users\mklod\AppData\Local"));
    base.join("razer-joro").join("daemon.log")
}

/// Open (or create) the log file and redirect the process's stderr and
/// stdout handles to it. Safe to call once at startup; subsequent
/// eprintln! / println! calls go to the file. Call BEFORE any logging
/// statements — anything written earlier is lost.
pub fn init() {
    let path = log_path();
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }

    // Rotate if the file is oversize. Ignore errors — a missing/locked
    // rotation target shouldn't prevent logging.
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > LOG_MAX_BYTES {
            let rotated = path.with_extension("log.1");
            let _ = std::fs::remove_file(&rotated);
            let _ = std::fs::rename(&path, &rotated);
        }
    }

    let file = match std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
    {
        Ok(f) => f,
        Err(_) => return,
    };

    let raw = file.into_raw_handle() as isize;
    let handle = HANDLE(raw as *mut _);
    unsafe {
        let _ = SetStdHandle(STD_ERROR_HANDLE, handle);
        let _ = SetStdHandle(STD_OUTPUT_HANDLE, handle);
    }
    // into_raw_handle() already transferred ownership — the OS now owns
    // the handle via SetStdHandle and keeps it valid for the process's
    // lifetime. No Drop to worry about.
}

/// Print a startup banner with timestamp so sessions are separable in
/// the tailed log file.
pub fn banner() {
    let ts = chrono_like_now();
    eprintln!("────────── joro-daemon starting {ts} ──────────");
}

fn chrono_like_now() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Very rough local-time format — don't pull chrono just for a banner.
    // Seconds-since-epoch is enough to correlate with external timestamps.
    format!("(epoch={secs})")
}
