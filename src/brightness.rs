// src/brightness.rs — external-monitor brightness control via DDC/CI.
// Last modified: 2026-04-15--0347
//
// Why this module exists: Windows' standard brightness controls (the OSD
// slider, `VK_BRIGHTNESS_UP/DOWN`, `WmiMonitorBrightnessMethods`) only
// drive *internal* laptop panels. On a desktop with an external monitor
// the OSD appears but nothing happens, because the host has no direct way
// to command brightness on a third-party panel. External monitors that
// support DDC/CI expose a VCP feature code 0x10 on the I2C channel
// embedded in the video cable — that's the "brightness" register.
//
// Microsoft's Monitor Configuration API wraps DDC/CI for us:
//   user32!EnumDisplayMonitors          — enumerate physical monitors
//   dxva2!GetPhysicalMonitorsFromHMONITOR — open each one
//   dxva2!GetMonitorBrightness          — read min/current/max (0-100)
//   dxva2!SetMonitorBrightness          — write new level
//   dxva2!DestroyPhysicalMonitors       — cleanup
//
// All the heavy lifting happens in dxva2.sys which actually speaks DDC/CI
// over the GPU's I2C bus. This is how Monitorian, Twinkle Tray, and every
// other "external monitor brightness" tool on Windows does it.

use std::mem::size_of;
use windows::core::Result as WinResult;
use windows::Win32::Foundation::{BOOL, LPARAM, RECT};
use windows::Win32::Devices::Display::{
    CapabilitiesRequestAndCapabilitiesReply, DestroyPhysicalMonitors,
    GetCapabilitiesStringLength, GetMonitorBrightness, GetPhysicalMonitorsFromHMONITOR,
    GetVCPFeatureAndVCPFeatureReply, SetMonitorBrightness, SetVCPFeature, PHYSICAL_MONITOR,
};
use windows::Win32::Graphics::Gdi::{
    EnumDisplayMonitors, GetMonitorInfoW, HDC, HMONITOR, MONITORINFO,
};

/// Enumerate all HMONITORs the system can see.
fn enum_monitors() -> Vec<HMONITOR> {
    unsafe extern "system" fn cb(h: HMONITOR, _hdc: HDC, _rect: *mut RECT, data: LPARAM) -> BOOL {
        let v = &mut *(data.0 as *mut Vec<HMONITOR>);
        v.push(h);
        BOOL(1)
    }
    let mut out: Vec<HMONITOR> = Vec::new();
    unsafe {
        let _ = EnumDisplayMonitors(
            HDC::default(),
            None,
            Some(cb),
            LPARAM(&mut out as *mut _ as isize),
        );
    }
    out
}

fn monitor_friendly(h: HMONITOR) -> String {
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    unsafe {
        let _ = GetMonitorInfoW(h, &mut info);
    }
    format!(
        "HMONITOR 0x{:x} {}x{}",
        h.0 as usize,
        info.rcMonitor.right - info.rcMonitor.left,
        info.rcMonitor.bottom - info.rcMonitor.top,
    )
}

/// A single physical monitor with its current min/cur/max brightness.
/// Owned handle — must be closed via `DestroyPhysicalMonitors` on drop.
pub struct PhysicalMonitor {
    pm: PHYSICAL_MONITOR,
    pub min: u32,
    pub cur: u32,
    pub max: u32,
    pub friendly: String,
}

impl PhysicalMonitor {
    /// Open every physical monitor for every HMONITOR. Monitors that
    /// don't support DDC/CI brightness are silently skipped.
    ///
    /// Uses `GetMonitorBrightness` as the filter because empirically
    /// that's the sequence the known-working `brightness vcp 10 = N`
    /// CLI path used when it first dimmed the user's Falcon cleanly.
    /// An earlier attempt to swap this for `GetVCPFeatureAndVCPFeatureReply`
    /// caused the Falcon to full-reboot on subsequent writes.
    pub fn enumerate() -> Vec<PhysicalMonitor> {
        let mut out = Vec::new();
        for hm in enum_monitors() {
            let friendly_hm = monitor_friendly(hm);
            let mut count: u32 = 0;
            unsafe {
                use windows::Win32::Devices::Display::GetNumberOfPhysicalMonitorsFromHMONITOR;
                if GetNumberOfPhysicalMonitorsFromHMONITOR(hm, &mut count).is_err() || count == 0 {
                    continue;
                }
                let mut phys: Vec<PHYSICAL_MONITOR> =
                    vec![PHYSICAL_MONITOR::default(); count as usize];
                if GetPhysicalMonitorsFromHMONITOR(hm, &mut phys).is_err() {
                    continue;
                }
                for pm in phys {
                    let (mut mn, mut cu, mut mx) = (0u32, 0u32, 0u32);
                    let r = GetMonitorBrightness(pm.hPhysicalMonitor, &mut mn, &mut cu, &mut mx);
                    if r == 0 {
                        let _ = DestroyPhysicalMonitors(&[pm]);
                        continue;
                    }
                    out.push(PhysicalMonitor {
                        pm,
                        min: mn,
                        cur: cu,
                        max: mx,
                        friendly: friendly_hm.clone(),
                    });
                }
            }
        }
        out
    }

    /// Read the MCCS capability string — a parenthesised S-expression the
    /// monitor returns via DDC/CI advertising its model, supported VCP
    /// feature codes, and value ranges. Example:
    ///   (prot(monitor)type(lcd)model(LG HDR WQHD)
    ///    cmds(01 02 03 07 0C E3 F3)
    ///    vcp(02 04 05 08 10 12 16 18 1A 52 60(0F 11 12 0F) B6 ...))
    /// The `vcp(...)` list is the authoritative "what this monitor
    /// actually supports". Codes we care about:
    ///   0x10 = Luminance (brightness) — MCCS standard
    ///   0x12 = Contrast
    ///   0x6B = Backlight Level (White) — sometimes "real" backlight
    ///   0x8D = Audio Mute
    ///   0x8F = Audio Volume
    pub fn capability_string(&self) -> Option<String> {
        unsafe {
            let mut len: u32 = 0;
            if GetCapabilitiesStringLength(self.pm.hPhysicalMonitor, &mut len) == 0 || len == 0 {
                return None;
            }
            let mut buf = vec![0u8; len as usize];
            if CapabilitiesRequestAndCapabilitiesReply(
                self.pm.hPhysicalMonitor,
                &mut buf,
            ) == 0
            {
                return None;
            }
            // Drop trailing NUL
            if let Some(pos) = buf.iter().position(|&b| b == 0) {
                buf.truncate(pos);
            }
            String::from_utf8(buf).ok()
        }
    }

    /// Low-level: read a raw VCP feature value. Returns (current, max).
    pub fn vcp_get(&self, code: u8) -> Option<(u32, u32)> {
        unsafe {
            let mut cur: u32 = 0;
            let mut max: u32 = 0;
            let r = GetVCPFeatureAndVCPFeatureReply(
                self.pm.hPhysicalMonitor,
                code,
                None,
                &mut cur,
                Some(&mut max),
            );
            if r == 0 {
                None
            } else {
                Some((cur, max))
            }
        }
    }

    /// Low-level: write a raw VCP feature value.
    pub fn vcp_set(&self, code: u8, value: u32) -> WinResult<()> {
        unsafe {
            let r = SetVCPFeature(self.pm.hPhysicalMonitor, code, value);
            if r == 0 {
                return Err(windows::core::Error::from_win32());
            }
            Ok(())
        }
    }

    /// Call Windows's high-level `SetMonitorBrightness` wrapper. Unlike
    /// `vcp_set(0x10, v)` which encodes a raw VCP command, this goes
    /// through Windows's own DDC/CI transaction path. Sometimes produces
    /// different packet sequences on the wire — worth trying as a
    /// fallback when raw VCP 0x10 writes are being silently dropped.
    pub fn set_monitor_brightness(&self, value: u32) -> WinResult<()> {
        unsafe {
            let r = SetMonitorBrightness(self.pm.hPhysicalMonitor, value);
            if r == 0 {
                return Err(windows::core::Error::from_win32());
            }
            Ok(())
        }
    }
}

impl Drop for PhysicalMonitor {
    fn drop(&mut self) {
        unsafe {
            let _ = DestroyPhysicalMonitors(&[self.pm]);
        }
    }
}

/// Serialize concurrent brightness adjustments. Each call enumerates
/// fresh, reads the monitor's current value, and issues a single
/// absolute write. The mutex prevents two threads racing each other's
/// reads and writes against the same DDC/I²C channel.
///
/// History note (2026-04-21): an earlier version cached the
/// PhysicalMonitor handle and `last_target` value across calls to avoid
/// per-press re-enumeration overhead. That cache was the source of
/// every silent-drop / monitor-reboot / "brightness broken" report —
/// when the cache drifted from monitor reality (display-mode change,
/// daemon restart, dropped write), every press generated absolute
/// writes far from the actual brightness, which crashed/locked the
/// G91SD's scaler. The cache and all of its workaround layers
/// (stepped 1-unit writes, verify-read, transition windows, power-event
/// listener, contrast-as-warm-up writes) are gone. See
/// `MONITOR_DEBUG_NOTES.md` for the empirical characterization that
/// proved per-press re-enumeration is fast enough and bulletproof.
static BRIGHTNESS_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Open the first DDC/CI-capable monitor and read its current state.
/// Returns None if no monitor is available. Caller is responsible for
/// the lifetime — `PhysicalMonitor::Drop` releases the handle.
fn open_first_monitor() -> Option<PhysicalMonitor> {
    let mut monitors = PhysicalMonitor::enumerate();
    if monitors.is_empty() {
        eprintln!("brightness: no DDC/CI-capable monitors found");
        return None;
    }
    Some(monitors.swap_remove(0))
}

/// Shift the first DDC/CI-capable monitor's brightness by `delta`
/// percent of its available range. Returns the number of monitors
/// adjusted (0 or 1). Always reads fresh from the monitor before
/// writing — no cache.
pub fn delta_all(delta: i32) -> usize {
    let _guard = BRIGHTNESS_LOCK.lock().unwrap();
    let m = match open_first_monitor() { Some(m) => m, None => return 0 };
    let range = m.max as i32 - m.min as i32;
    if range <= 0 { return 0; }
    let step = (range * delta / 100).abs().max(1);
    let new_val = if delta >= 0 {
        (m.cur as i32 + step).clamp(m.min as i32, m.max as i32)
    } else {
        (m.cur as i32 - step).clamp(m.min as i32, m.max as i32)
    } as u32;
    eprintln!("brightness: {} {} -> {} (range {}..{})",
        m.friendly, m.cur, new_val, m.min, m.max);
    if let Err(e) = m.vcp_set(0x10, new_val) {
        eprintln!("brightness: write {new_val} failed: {e}");
        return 0;
    }
    1
}

/// Set absolute brightness as percent of the monitor's reported range.
/// Reads fresh range from the monitor each call. Returns the number of
/// monitors adjusted (0 or 1).
pub fn set_all_percent(percent: u32) -> usize {
    let _guard = BRIGHTNESS_LOCK.lock().unwrap();
    let m = match open_first_monitor() { Some(m) => m, None => return 0 };
    let p = percent.min(100);
    let range = m.max.saturating_sub(m.min);
    let target = m.min + (range * p / 100);
    eprintln!("brightness: {} {} -> {} ({}%)", m.friendly, m.cur, target, p);
    if let Err(e) = m.vcp_set(0x10, target) {
        eprintln!("brightness: write {target} failed: {e}");
        return 0;
    }
    1
}
