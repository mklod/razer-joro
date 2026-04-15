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
                        // DDC/CI not supported on this monitor — close + skip
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

    pub fn set(&mut self, level: u32) -> WinResult<()> {
        let clamped = level.clamp(self.min, self.max);
        unsafe {
            let r = SetMonitorBrightness(self.pm.hPhysicalMonitor, clamped);
            if r == 0 {
                return Err(windows::core::Error::from_win32());
            }
        }
        self.cur = clamped;
        Ok(())
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

/// Shift every DDC/CI-capable monitor's brightness by `delta` percent of
/// its available range, clamped to the monitor's reported min/max. Logs
/// per-monitor failures and returns the number of monitors successfully
/// adjusted.
pub fn delta_all(delta: i32) -> usize {
    let monitors = PhysicalMonitor::enumerate();
    if monitors.is_empty() {
        eprintln!("brightness: no DDC/CI-capable monitors found");
        return 0;
    }
    let mut ok = 0usize;
    for mut m in monitors {
        let range = m.max as i32 - m.min as i32;
        if range <= 0 {
            continue;
        }
        let step = (range * delta / 100).abs().max(1);
        let new_val = if delta >= 0 {
            (m.cur as i32 + step).clamp(m.min as i32, m.max as i32)
        } else {
            (m.cur as i32 - step).clamp(m.min as i32, m.max as i32)
        } as u32;
        match m.set(new_val) {
            Ok(()) => {
                eprintln!(
                    "brightness: {} → {} (range {}..{})",
                    m.friendly, new_val, m.min, m.max
                );
                ok += 1;
            }
            Err(e) => eprintln!("brightness: set {} failed: {e}", m.friendly),
        }
    }
    ok
}

/// Absolute set: clamp to each monitor's min/max and write.
pub fn set_all_percent(percent: u32) -> usize {
    let p = percent.min(100);
    let monitors = PhysicalMonitor::enumerate();
    if monitors.is_empty() {
        eprintln!("brightness: no DDC/CI-capable monitors found");
        return 0;
    }
    let mut ok = 0usize;
    for mut m in monitors {
        let range = m.max.saturating_sub(m.min);
        let target = m.min + (range * p / 100);
        match m.set(target) {
            Ok(()) => {
                eprintln!("brightness: {} → {} ({}%)", m.friendly, target, p);
                ok += 1;
            }
            Err(e) => eprintln!("brightness: set {} failed: {e}", m.friendly),
        }
    }
    ok
}
