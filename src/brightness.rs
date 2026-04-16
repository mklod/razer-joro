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
    GetVCPFeatureAndVCPFeatureReply, SetVCPFeature, PHYSICAL_MONITOR,
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
}

impl Drop for PhysicalMonitor {
    fn drop(&mut self) {
        unsafe {
            let _ = DestroyPhysicalMonitors(&[self.pm]);
        }
    }
}

/// Step VCP 0x10 one unit at a time from `start` to `target` with a
/// small sleep between each write. Some monitors (Falcon 5120x1440)
/// full-reboot their scaler if a DDC/CI brightness change exceeds a
/// few units in a single write, but tolerate rapid single-step writes
/// just fine. 5ms per step means a full 0-50 sweep takes ~250ms.
/// Returns true if all writes succeeded, false if any failed (caller
/// should invalidate the cached handle on false).
fn stepped_write(m: &PhysicalMonitor, start: u32, target: u32) -> bool {
    let mut v = start as i32;
    let end = target as i32;
    let dir: i32 = if end > v { 1 } else if end < v { -1 } else { return true };
    while v != end {
        v += dir;
        if let Err(e) = m.vcp_set(0x10, v as u32) {
            eprintln!("brightness: stepped write {v} failed: {e} — will re-enumerate next time");
            return false;
        }
        std::thread::sleep(std::time::Duration::from_millis(20));
    }
    true
}

/// Global serialization + brightness state. All brightness adjustments
/// go through this lock so rapid taps don't race. We cache BOTH the
/// last-applied VCP value AND the PhysicalMonitor handle — reusing the
/// handle avoids re-calling `GetMonitorBrightness` (which is a separate
/// dxva2 DDC/CI transaction) on every keypress. The Falcon 5120x1440
/// reboots its scaler when DDC/CI reads are interleaved with rapid
/// writes; caching the handle eliminates the reads entirely after the
/// first enumeration.
struct BrightnessState {
    monitor: PhysicalMonitor,
    last_target: u32,
}
// SAFETY: PhysicalMonitor holds a raw HANDLE which is just a kernel
// object ID — safe to access from any thread. The dxva2 API calls
// (SetVCPFeature, GetMonitorBrightness) are thread-safe per MSDN.
unsafe impl Send for BrightnessState {}
static BRIGHTNESS_STATE: std::sync::Mutex<Option<BrightnessState>> = std::sync::Mutex::new(None);

/// Ensure the global BrightnessState is initialized. Called under the
/// lock. Returns false if no DDC/CI monitor is available.
fn ensure_state(state: &mut Option<BrightnessState>) -> bool {
    if state.is_some() {
        return true;
    }
    let monitors = PhysicalMonitor::enumerate();
    if monitors.is_empty() {
        eprintln!("brightness: no DDC/CI-capable monitors found");
        return false;
    }
    // Take ownership of the first monitor. The rest are dropped
    // (DestroyPhysicalMonitors runs in their Drop impl).
    let mut monitors = monitors;
    let m = monitors.swap_remove(0);
    let cur = m.cur;
    *state = Some(BrightnessState {
        monitor: m,
        last_target: cur,
    });
    true
}

/// Shift every DDC/CI-capable monitor's brightness by `delta` percent
/// of its available range. The monitor handle is cached globally so
/// we don't re-enumerate (and re-call `GetMonitorBrightness`) on
/// every keypress — that extra DDC/CI transaction was causing the
/// Falcon to reboot its scaler under rapid repeated presses.
pub fn delta_all(delta: i32) -> usize {
    let mut guard = BRIGHTNESS_STATE.lock().unwrap();
    if !ensure_state(&mut guard) {
        return 0;
    }
    let s = guard.as_mut().unwrap();
    let range = s.monitor.max as i32 - s.monitor.min as i32;
    if range <= 0 { return 0; }
    let step = (range * delta / 100).abs().max(1);
    let new_val = if delta >= 0 {
        (s.last_target as i32 + step).clamp(s.monitor.min as i32, s.monitor.max as i32)
    } else {
        (s.last_target as i32 - step).clamp(s.monitor.min as i32, s.monitor.max as i32)
    } as u32;
    eprintln!("brightness: {} ramping {} -> {} (range {}..{})",
        s.monitor.friendly, s.last_target, new_val, s.monitor.min, s.monitor.max);
    if !stepped_write(&s.monitor, s.last_target, new_val) {
        // Handle went stale (monitor rebooted / re-enumerated).
        // Drop the cached state so the next press re-enumerates.
        *guard = None;
        return 0;
    }
    s.last_target = new_val;
    1
}

/// Absolute set: map `percent` (0-100) onto the monitor's reported
/// min/max range and ramp via stepped writes. Monitor handle is
/// cached; no re-enumeration.
pub fn set_all_percent(percent: u32) -> usize {
    let mut guard = BRIGHTNESS_STATE.lock().unwrap();
    if !ensure_state(&mut guard) {
        return 0;
    }
    let s = guard.as_mut().unwrap();
    let p = percent.min(100);
    let range = s.monitor.max.saturating_sub(s.monitor.min);
    let target = s.monitor.min + (range * p / 100);
    eprintln!("brightness: {} ramping {} -> {} ({}%)",
        s.monitor.friendly, s.last_target, target, p);
    if !stepped_write(&s.monitor, s.last_target, target) {
        *guard = None;
        return 0;
    }
    s.last_target = target;
    1
}
