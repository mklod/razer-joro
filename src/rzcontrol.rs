// src/rzcontrol.rs — Razer RzDev_02ce filter driver client
// Last modified: 2026-04-14--2200
//
// Opens the Razer lower-filter driver's user-mode control device and
// installs scancode hooks + reader/injector loop to give Joro's F-row
// Fn-primary behavior (plain VK_F5..VK_F12) over BLE, replicating what
// Razer Synapse does. See memory/project_razer_filter_driver_ioctls.md
// for the full protocol.
//
// REQUIRES: Razer Synapse must run once per Windows session to wire up
// the filter driver's internal state. After that Synapse can be killed
// and this daemon takes over. Our PoC cannot fully init the filter from
// a cold driver state — an unknown Synapse init step is still missing.
//
// Architecture:
//  - `RzControl::open()` opens the rzcontrol device, enables the filter,
//    installs SetInputHook rules, installs consumer-usage filters, and
//    spawns a reader thread.
//  - The reader thread blocks on `DeviceIoControl(0x88883018)` which
//    returns one keyboard event per completion. Event record starts at
//    offset 0x10: `[u32 type=1][u16 0][u16 sc][u16 state 0=down 1=up]`.
//  - For each scancode in FN_PRIMARY_SCANCODES, the reader re-injects it
//    via `DeviceIoControl(0x88883020 cmd=1)` which kernel-side emits the
//    scancode back into kbdhid, bypassing the filter. Windows sees plain
//    VK_Fx.
//  - Drop signals the reader thread to stop, unhooks every scancode, and
//    closes the handle.

use std::ffi::c_void;
use std::mem::{size_of, zeroed};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use windows::core::{GUID, PCWSTR};
use windows::Win32::Devices::DeviceAndDriverInstallation::{
    SetupDiDestroyDeviceInfoList, SetupDiEnumDeviceInterfaces, SetupDiGetClassDevsW,
    SetupDiGetDeviceInterfaceDetailW, DIGCF_DEVICEINTERFACE, DIGCF_PRESENT,
    HDEVINFO, SP_DEVICE_INTERFACE_DATA, SP_DEVICE_INTERFACE_DETAIL_DATA_W,
};
use windows::Win32::Foundation::{CloseHandle, GENERIC_READ, GENERIC_WRITE, HANDLE};
use windows::Win32::Storage::FileSystem::{
    CreateFileW, FILE_ATTRIBUTE_NORMAL, FILE_SHARE_READ, FILE_SHARE_WRITE, OPEN_EXISTING,
};
use windows::Win32::System::IO::DeviceIoControl;

// {e3be005d-d130-4910-88ff-09ae02f680e9}
const RZCONTROL_GUID: GUID = GUID::from_u128(0xe3be005d_d130_4910_88ff_09ae02f680e9);

// IOCTLs captured via Frida on NtDeviceIoControlFile, device type 0x8888,
// METHOD_BUFFERED, FILE_ANY_ACCESS.
const IOCTL_READ_EVENT: u32 = 0x88883018; // out: 304-byte event record (offset 0x10)
const IOCTL_HYPERSHIFT_NOTIFY: u32 = 0x8888301c; // in: 5 bytes [01 00 00 00 enable]
const IOCTL_CMD: u32 = 0x88883020; // in: 32-byte command (offset 4 = tag)
const IOCTL_SET_INPUT_HOOK: u32 = 0x88883024; // in: 292-byte struct, out: 0
const IOCTL_ENABLE_INPUT_HOOK: u32 = 0x88883034; // in: u32 bool
const IOCTL_ENABLE_INPUT_NOTIFY: u32 = 0x88883038; // in: u32 bool

// Command tags for IOCTL_CMD (0x88883020)
const CMD_INJECT_SCANCODE: u8 = 0x01;
const CMD_CONSUMER_FILTER: u8 = 0x0a;

// Consumer HID usages we install filters for so F8/F9 brightness doesn't
// leak through the consumer channel. See captures/rzctl_init_2026-04-14.log.
const CONSUMER_FILTER_USAGES: &[u16] = &[
    0x0070, // BrightnessDown (Joro F8 MM)
    0x006f, // BrightnessUp   (Joro F9 MM)
];

/// PS/2 Set 1 scancodes for Joro F-row keys.
pub mod sc {
    pub const ESC: u16 = 0x01;
    pub const TAB: u16 = 0x0f;
    pub const LALT: u16 = 0x38;
    pub const F1: u16 = 0x3b;
    pub const F2: u16 = 0x3c;
    pub const F3: u16 = 0x3d;
    pub const F4: u16 = 0x3e;
    pub const F5: u16 = 0x3f;
    pub const F6: u16 = 0x40;
    pub const F7: u16 = 0x41;
    pub const F8: u16 = 0x42;
    pub const F9: u16 = 0x43;
    pub const F10: u16 = 0x44;
    pub const F11: u16 = 0x57;
    pub const F12: u16 = 0x58;
}

/// Candidate paths for RazerAppEngine.exe — checked in order, first hit
/// wins. Versioned subdirs are fallbacks in case the top-level symlink/
/// stub is missing on a given Razer install.
const RAZER_APP_ENGINE_PATHS: &[&str] = &[
    r"C:\Program Files\Razer\RazerAppEngine\RazerAppEngine.exe",
    r"C:\Program Files\Razer\RazerAppEngine\app-4.0.662\RazerAppEngine.exe",
    r"C:\Program Files\Razer\RazerAppEngine\app-4.0.660\RazerAppEngine.exe",
];

/// Launch RazerAppEngine briefly to prime the filter driver's init state,
/// then kill it so we can take over rzcontrol. Required because our
/// daemon doesn't know whatever init step Synapse does to fully arm the
/// filter — after a DisableInputHook or fresh Windows boot, only Synapse
/// can wire up the filter. Piggyback on its init, then kill it so its
/// background rule-overwriter doesn't fight our writes.
///
/// If RazerAppEngine is already running (user is actively using
/// Synapse), we skip entirely and return `Ok(false)` so the caller can
/// decide what to do.
pub fn bootstrap_filter_driver(settle_secs: u64) -> Result<bool, String> {
    // Skip if RazerAppEngine.exe is already running.
    let already = Command::new("tasklist")
        .args(["/FI", "IMAGENAME eq RazerAppEngine.exe", "/FO", "CSV", "/NH"])
        .output()
        .map_err(|e| format!("tasklist: {e}"))?;
    let stdout = String::from_utf8_lossy(&already.stdout);
    if stdout.to_ascii_lowercase().contains("razerappengine.exe") {
        eprintln!("rzcontrol: RazerAppEngine.exe already running — skipping bootstrap");
        return Ok(false);
    }

    // Find a usable RazerAppEngine.exe path.
    let exe = RAZER_APP_ENGINE_PATHS
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .ok_or_else(|| "RazerAppEngine.exe not found in any known location".to_string())?;

    eprintln!("rzcontrol: bootstrap launching {exe}");
    // Detached spawn. `start` via cmd so the child isn't tied to us.
    Command::new("cmd")
        .args(["/C", "start", "", exe])
        .spawn()
        .map_err(|e| format!("spawn RazerAppEngine: {e}"))?;

    // Let Synapse's init walk through: it takes ~5 s for `EnableInputHook`
    // + `SetInputHook` rounds to land. Configurable via `settle_secs`.
    let start = Instant::now();
    while start.elapsed() < Duration::from_secs(settle_secs) {
        thread::sleep(Duration::from_millis(250));
    }

    // Kill the entire tree. Using taskkill /T because RazerAppEngine
    // spawns many child processes.
    let _ = Command::new("taskkill")
        .args(["/F", "/IM", "RazerAppEngine.exe", "/T"])
        .output()
        .map_err(|e| format!("taskkill RazerAppEngine: {e}"))?;

    // Settle for the driver to notice handles dropping and the filter
    // rules to become ours to write.
    thread::sleep(Duration::from_millis(500));
    eprintln!("rzcontrol: bootstrap done ({:.1}s)", start.elapsed().as_secs_f32());
    Ok(true)
}

/// Scancodes hooked when `ble_fn_primary = true`.
///
/// Excludes F1/F2/F3 — those are BLE slot selectors handled below the
/// HID stack in firmware and the filter never sees them (verified
/// 2026-04-14). Also excludes F4 — firmware already emits Win+Tab which
/// the existing `consumer_remap`/`fn_host_remap` paths handle.
pub const FN_PRIMARY_SCANCODES: &[u16] = &[
    sc::F5, sc::F6, sc::F7, sc::F8, sc::F9, sc::F10, sc::F11, sc::F12,
];

/// Wrapper so `HANDLE` can cross thread boundaries. The rzcontrol handle
/// is safe to use from multiple threads concurrently (DeviceIoControl is
/// kernel-synchronized).
#[derive(Copy, Clone)]
struct RawHandle(HANDLE);
unsafe impl Send for RawHandle {}
unsafe impl Sync for RawHandle {}

/// A live rzcontrol session: handle is held open, reader thread runs,
/// and Drop signals the reader, unhooks rules, removes consumer filters,
/// and closes the device.
pub struct RzControl {
    handle: HANDLE,
    hooked: Vec<u16>,
    reader_stop: Arc<AtomicBool>,
    reader_thread: Option<JoinHandle<()>>,
    /// Scancodes the reader owns (events on these get re-injected). We
    /// keep a copy so the reader thread has its own slice.
    owned_scancodes: Vec<u16>,
}

impl RzControl {
    /// Open the rzcontrol device and enable the filter. Does NOT install
    /// hooks or start the reader — call `hook_all` for that.
    pub fn open() -> Result<Self, String> {
        let path = find_joro_rzcontrol_path()?;
        // SAFETY: path is a NUL-terminated UTF-16 string produced by find_.
        let handle = unsafe {
            CreateFileW(
                PCWSTR(path.as_ptr()),
                GENERIC_READ.0 | GENERIC_WRITE.0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
        }
        .map_err(|e| format!("CreateFileW(rzcontrol): {e}"))?;

        let me = RzControl {
            handle,
            hooked: Vec::new(),
            reader_stop: Arc::new(AtomicBool::new(false)),
            reader_thread: None,
            owned_scancodes: Vec::new(),
        };
        me.enable_filter(true)?;
        Ok(me)
    }

    fn enable_filter(&self, enable: bool) -> Result<(), String> {
        let val: u32 = if enable { 1 } else { 0 };
        self.ioctl(IOCTL_ENABLE_INPUT_HOOK, &val.to_le_bytes())
            .map_err(|e| format!("EnableInputHook: {e}"))?;
        self.ioctl(IOCTL_ENABLE_INPUT_NOTIFY, &val.to_le_bytes())
            .map_err(|e| format!("EnableInputNotify: {e}"))?;
        Ok(())
    }

    /// Enable / disable Hypershift-state notifications via the
    /// `0x8888301c` IOCTL. Captured Synapse pattern (during a Hypershift
    /// edit session): two calls bracketing the edit — first `[01,0,0,0,01]`
    /// to enable, then `[01,0,0,0,00]` when done.
    ///
    /// Hypothesized to make the filter deliver Fn-key state events through
    /// the standard `0x88883018` read channel. Verified empirically
    /// 2026-04-24 via Frida (`captures/frida_hypershift_full.log`).
    fn enable_hypershift_notify(&self, enable: bool) -> Result<(), String> {
        let mut buf = [0u8; 5];
        buf[0] = 0x01;
        // bytes 1-3 stay zero
        buf[4] = if enable { 0x01 } else { 0x00 };
        self.ioctl(IOCTL_HYPERSHIFT_NOTIFY, &buf)
            .map_err(|e| format!("HypershiftNotify({enable}): {e}"))
    }

    /// Open the rzcontrol device for **observer-only Fn-detection** through
    /// the dongle. Does NOT call `EnableInputHook` (which would block all
    /// keys), only enables Hypershift notifications. The reader thread can
    /// then watch the event stream for Fn-state events without affecting
    /// normal keyboard delivery. Use `start_observer_thread` to spawn the
    /// reader.
    pub fn open_observer() -> Result<Self, String> {
        let path = find_joro_rzcontrol_path()?;
        let handle = unsafe {
            CreateFileW(
                PCWSTR(path.as_ptr()),
                GENERIC_READ.0 | GENERIC_WRITE.0,
                FILE_SHARE_READ | FILE_SHARE_WRITE,
                None,
                OPEN_EXISTING,
                FILE_ATTRIBUTE_NORMAL,
                None,
            )
        }
        .map_err(|e| format!("CreateFileW(rzcontrol observer): {e}"))?;

        let me = RzControl {
            handle,
            hooked: Vec::new(),
            reader_stop: Arc::new(AtomicBool::new(false)),
            reader_thread: None,
            owned_scancodes: Vec::new(),
        };
        // Enable Hypershift notifications so Fn-state events are delivered
        // through the standard 0x88883018 read channel.
        me.enable_hypershift_notify(true)?;
        Ok(me)
    }

    /// Spawn an observer thread that just LOGS every event from
    /// `0x88883018` — no scancode filtering, no injection. Used to discover
    /// the Fn-state event format on dongle.
    pub fn start_observer_thread(&mut self) {
        if self.reader_thread.is_some() {
            return;
        }
        let stop = self.reader_stop.clone();
        let h = RawHandle(self.handle);
        let t = thread::spawn(move || observer_loop(h, stop));
        self.reader_thread = Some(t);
    }

    fn set_hook(&self, scancode: u16, active: bool) -> Result<(), String> {
        let mut buf = [0u8; 292];
        buf[4] = if active { 1 } else { 0 };
        buf[0x0a] = (scancode & 0xff) as u8;
        buf[0x0b] = ((scancode >> 8) & 0xff) as u8;
        self.ioctl(IOCTL_SET_INPUT_HOOK, &buf)
            .map_err(|e| format!("SetInputHook(sc=0x{scancode:02x}, active={active}): {e}"))
    }

    fn install_consumer_filter(&self, usage: u16) -> Result<(), String> {
        // 32B buffer. [u32 0][u32 cmd=0x0a][u16 usage][u16 0][u64 0][u64 0]
        let mut buf = [0u8; 32];
        buf[4] = CMD_CONSUMER_FILTER;
        buf[0x08] = (usage & 0xff) as u8;
        buf[0x09] = ((usage >> 8) & 0xff) as u8;
        self.ioctl(IOCTL_CMD, &buf)
            .map_err(|e| format!("cmd=0x0a install(usage=0x{usage:04x}): {e}"))
    }

    fn remove_consumer_filter(&self, _usage: u16) {
        // Best-effort remove: cmd=0x0a with usage=0 (matches Synapse cleanup pattern).
        let mut buf = [0u8; 32];
        buf[4] = CMD_CONSUMER_FILTER;
        let _ = self.ioctl(IOCTL_CMD, &buf);
    }

    /// Hook every scancode in `scancodes`, install the consumer-usage
    /// filters, and spawn the reader thread. The reader blocks on
    /// `0x88883018` and calls `cmd=1` inject on each target scancode.
    pub fn hook_all(&mut self, scancodes: &[u16]) -> Result<(), String> {
        for &sc in scancodes {
            self.set_hook(sc, true)?;
            if !self.hooked.contains(&sc) {
                self.hooked.push(sc);
            }
        }
        for &usage in CONSUMER_FILTER_USAGES {
            if let Err(e) = self.install_consumer_filter(usage) {
                eprintln!("rzcontrol: {e}");
            }
        }
        self.owned_scancodes = scancodes.to_vec();
        self.start_reader_thread();
        Ok(())
    }

    fn start_reader_thread(&mut self) {
        if self.reader_thread.is_some() {
            return;
        }
        let stop = self.reader_stop.clone();
        let h = RawHandle(self.handle);
        let owned = self.owned_scancodes.clone();
        let t = thread::spawn(move || reader_loop(h, owned, stop));
        self.reader_thread = Some(t);
    }

    /// Unhook every scancode currently installed + remove consumer filters.
    fn unhook_all(&mut self) {
        let scs: Vec<u16> = std::mem::take(&mut self.hooked);
        for sc in scs {
            let _ = self.set_hook(sc, false);
        }
        for &usage in CONSUMER_FILTER_USAGES {
            self.remove_consumer_filter(usage);
        }
    }

    fn ioctl(&self, code: u32, input: &[u8]) -> Result<(), String> {
        let mut bytes_returned: u32 = 0;
        // SAFETY: handle is a valid rzcontrol device handle, input is a
        // valid slice, output buffer is None (no output expected).
        unsafe {
            DeviceIoControl(
                self.handle,
                code,
                Some(input.as_ptr() as *const c_void),
                input.len() as u32,
                None,
                0,
                Some(&mut bytes_returned),
                None,
            )
        }
        .map_err(|e| format!("DeviceIoControl(0x{code:08x}): {e}"))
    }
}

impl Drop for RzControl {
    fn drop(&mut self) {
        // Signal reader to stop and wait for it. CancelSynchronousIo or
        // CloseHandle would kick it out of the blocking DeviceIoControl;
        // we rely on CloseHandle below to wake it.
        self.reader_stop.store(true, Ordering::SeqCst);

        self.unhook_all();
        let _ = self.enable_filter(false);

        // Close the handle first — that aborts any blocking read in the
        // reader thread (returns err=OPERATION_ABORTED).
        // SAFETY: handle was created by CreateFileW and we own it.
        unsafe { let _ = CloseHandle(self.handle); }

        if let Some(t) = self.reader_thread.take() {
            // Best-effort join with a short timeout semantic — we can't
            // actually time out, so we just join. The thread exits quickly
            // after CloseHandle because DeviceIoControl returns an error.
            let _ = t.join();
        }
    }
}

/// Issue an IOCTL on `h` with no input (read-event path). Returns the
/// output buffer slice or an OS error code.
fn ioctl_read(h: HANDLE, code: u32, out: &mut [u8]) -> Result<u32, u32> {
    let mut bytes_returned: u32 = 0;
    // SAFETY: handle is valid, out is a valid mutable slice.
    let result = unsafe {
        DeviceIoControl(
            h,
            code,
            None,
            0,
            Some(out.as_mut_ptr() as *mut c_void),
            out.len() as u32,
            Some(&mut bytes_returned),
            None,
        )
    };
    match result {
        Ok(()) => Ok(bytes_returned),
        Err(e) => Err(e.code().0 as u32 & 0xFFFF),
    }
}

/// Issue an IOCTL on `h` with input only. For cmd=1 inject calls from
/// the reader thread.
fn ioctl_write(h: HANDLE, code: u32, input: &[u8]) -> Result<(), u32> {
    let mut bytes_returned: u32 = 0;
    // SAFETY: handle is valid, input is a valid slice.
    let result = unsafe {
        DeviceIoControl(
            h,
            code,
            Some(input.as_ptr() as *const c_void),
            input.len() as u32,
            None,
            0,
            Some(&mut bytes_returned),
            None,
        )
    };
    match result {
        Ok(()) => Ok(()),
        Err(e) => Err(e.code().0 as u32 & 0xFFFF),
    }
}

fn inject_scancode(h: HANDLE, scancode: u16, state: u16) -> Result<(), u32> {
    // 32B cmd=1 payload: [u32 0][u32 1][u16 0][u16 sc][u16 state][u16 0][u64 0]
    let mut buf = [0u8; 32];
    buf[4] = CMD_INJECT_SCANCODE;
    buf[0x0a] = (scancode & 0xff) as u8;
    buf[0x0b] = ((scancode >> 8) & 0xff) as u8;
    buf[0x0c] = (state & 0xff) as u8;
    buf[0x0d] = ((state >> 8) & 0xff) as u8;
    ioctl_write(h, IOCTL_CMD, &buf)
}

/// Reader thread: block on 0x88883018, parse the event at offset 0x10,
/// and if the scancode is one we own, re-inject it via cmd=1. Loops
/// until `stop` is set or the handle is closed.
fn reader_loop(h: RawHandle, owned: Vec<u16>, stop: Arc<AtomicBool>) {
    let mut out = [0u8; 304];
    let mut consecutive_errors: u32 = 0;
    while !stop.load(Ordering::Relaxed) {
        for b in out.iter_mut() {
            *b = 0;
        }
        match ioctl_read(h.0, IOCTL_READ_EVENT, &mut out) {
            Ok(_) => {
                consecutive_errors = 0;
                // Event record at offset 0x10:
                //   0x10 u32 type (1 = scancode)
                //   0x14 u16 zero
                //   0x16 u16 scancode
                //   0x18 u16 state (0=down, 1=up)
                let ev_type = u32::from_le_bytes(out[0x10..0x14].try_into().unwrap());
                let sc = u16::from_le_bytes(out[0x16..0x18].try_into().unwrap());
                let state = u16::from_le_bytes(out[0x18..0x1a].try_into().unwrap());

                // Diagnostic: log EVERY event so we can characterize what
                // events the dongle's filter driver delivers (esp. Fn key).
                // First 32 bytes of event record are most informative.
                let hex: String = out[0..32]
                    .iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<Vec<_>>()
                    .join(" ");
                eprintln!(
                    "rzcontrol-evt: type={} sc=0x{:04x} state={} hex32={}",
                    ev_type, sc, state, hex
                );

                if ev_type == 1 && (state == 0 || state == 1) && owned.contains(&sc) {
                    if let Err(e) = inject_scancode(h.0, sc, state) {
                        eprintln!("rzcontrol: inject sc=0x{sc:02x} state={state} failed err={e}");
                    }
                }
            }
            Err(err) => {
                if stop.load(Ordering::Relaxed) {
                    return;
                }
                // err=22 BAD_COMMAND and err=995 OPERATION_ABORTED are the
                // expected "queue drained" / "handle closing" signals. Back off.
                consecutive_errors = consecutive_errors.saturating_add(1);
                if consecutive_errors > 200 {
                    // Probably a permanent failure — log and bail.
                    eprintln!("rzcontrol: reader giving up after {consecutive_errors} errors (last err={err})");
                    return;
                }
                thread::sleep(Duration::from_millis(50));
            }
        }
    }
}

/// Observer reader: read events from rzcontrol forever, log everything.
/// Used to characterize the Fn-state event format on dongle. Does NOT
/// inject anything — purely diagnostic.
fn observer_loop(h: RawHandle, stop: Arc<AtomicBool>) {
    let mut out = [0u8; 304];
    let mut consecutive_errors: u32 = 0;
    while !stop.load(Ordering::Relaxed) {
        for b in out.iter_mut() {
            *b = 0;
        }
        match ioctl_read(h.0, IOCTL_READ_EVENT, &mut out) {
            Ok(_) => {
                consecutive_errors = 0;
                let ev_type = u32::from_le_bytes(out[0x10..0x14].try_into().unwrap());
                let marker = u16::from_le_bytes(out[0x14..0x16].try_into().unwrap());
                let sc = u16::from_le_bytes(out[0x16..0x18].try_into().unwrap());
                let state = u16::from_le_bytes(out[0x18..0x1a].try_into().unwrap());
                let hex32: String = out[0..32]
                    .iter()
                    .map(|b| format!("{:02x}", b))
                    .collect::<Vec<_>>()
                    .join(" ");
                eprintln!(
                    "rzctrl-observer: type={} marker=0x{:04x} sc=0x{:04x} state={} hex32={}",
                    ev_type, marker, sc, state, hex32
                );
            }
            Err(err) => {
                if stop.load(Ordering::Relaxed) {
                    return;
                }
                consecutive_errors = consecutive_errors.saturating_add(1);
                if consecutive_errors > 200 {
                    eprintln!("rzctrl-observer: giving up after {consecutive_errors} errors (last err={err})");
                    return;
                }
                thread::sleep(Duration::from_millis(50));
            }
        }
    }
}

/// Find the rzcontrol device interface path for Joro. Tries dongle path
/// first (`vid_1532&pid_009c`, the DA V2 X HyperSpeed multi-device dongle),
/// then BLE path (`vid_068e&pid_02ce`) as fallback. Synapse uses the same
/// rzcontrol filter driver IOCTLs against both paths — verified via Frida
/// 2026-04-24 (`captures/frida_hypershift_full.log`).
fn find_joro_rzcontrol_path() -> Result<Vec<u16>, String> {
    // SAFETY: passing a valid GUID ref; null params are allowed.
    let hinfo: HDEVINFO = unsafe {
        SetupDiGetClassDevsW(
            Some(&RZCONTROL_GUID),
            PCWSTR::null(),
            None,
            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE,
        )
    }
    .map_err(|e| format!("SetupDiGetClassDevsW: {e}"))?;

    let mut result = Err(
        "no rzcontrol device present for Joro (looked for VID_1532&PID_009C dongle and VID_068E&PID_02CE BLE)"
            .to_string(),
    );
    // Two passes: first prefer dongle path, then fall back to BLE path.
    // Walk the device list each pass.
    let mut found_dongle: Option<Vec<u16>> = None;
    let mut found_ble: Option<Vec<u16>> = None;
    let mut idx: u32 = 0;
    loop {
        let mut did: SP_DEVICE_INTERFACE_DATA = unsafe { zeroed() };
        did.cbSize = size_of::<SP_DEVICE_INTERFACE_DATA>() as u32;
        // SAFETY: hinfo is valid, did is a valid out pointer.
        let ok = unsafe {
            SetupDiEnumDeviceInterfaces(hinfo, None, &RZCONTROL_GUID, idx, &mut did)
        };
        if ok.is_err() {
            break;
        }

        // First call: get required size.
        let mut required: u32 = 0;
        // SAFETY: out-only call to probe size; detail buffer is None.
        let _ = unsafe {
            SetupDiGetDeviceInterfaceDetailW(
                hinfo, &did, None, 0, Some(&mut required), None,
            )
        };
        if required == 0 {
            idx += 1;
            continue;
        }

        // Allocate a byte buffer the driver wants, interpret the head as
        // SP_DEVICE_INTERFACE_DETAIL_DATA_W, read path from DevicePath[0].
        let mut buf = vec![0u8; required as usize];
        // SAFETY: head of buf is writable; we set cbSize before the call.
        let detail_ptr = buf.as_mut_ptr() as *mut SP_DEVICE_INTERFACE_DETAIL_DATA_W;
        unsafe {
            (*detail_ptr).cbSize = size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>() as u32;
        }
        // SAFETY: buffer is sized to `required`.
        let ok = unsafe {
            SetupDiGetDeviceInterfaceDetailW(
                hinfo, &did, Some(detail_ptr), required, None, None,
            )
        };
        if ok.is_ok() {
            // DevicePath starts at offset offsetof(cbSize) + 4 in the
            // struct. The windows-rs type has `DevicePath: [u16; 1]` as
            // the flexible array tail; read u16s from there until NUL.
            // SAFETY: detail_ptr is valid and buf extends past the struct.
            let path_ptr = unsafe { (*detail_ptr).DevicePath.as_ptr() };
            let mut len = 0usize;
            // Bound len by remaining buffer capacity to avoid OOB on a
            // malformed response.
            let cap = (required as usize)
                .saturating_sub(size_of::<SP_DEVICE_INTERFACE_DETAIL_DATA_W>() - 2)
                / 2;
            while len < cap {
                let c = unsafe { *path_ptr.add(len) };
                if c == 0 {
                    break;
                }
                len += 1;
            }
            let slice = unsafe { std::slice::from_raw_parts(path_ptr, len) };
            let path_str: String = String::from_utf16_lossy(slice).to_lowercase();
            // Dongle takes priority — same filter driver, but Joro through
            // dongle requires this path for input event monitoring.
            if path_str.contains("vid_1532") && path_str.contains("pid_009c")
                && found_dongle.is_none()
            {
                let mut v = slice.to_vec();
                v.push(0);
                found_dongle = Some(v);
            } else if path_str.contains("vid_068e") && path_str.contains("pid_02ce")
                && found_ble.is_none()
            {
                let mut v = slice.to_vec();
                v.push(0);
                found_ble = Some(v);
            }
        }
        idx += 1;
    }

    // SAFETY: hinfo is valid and not used after this.
    unsafe { let _ = SetupDiDestroyDeviceInfoList(hinfo); }

    if let Some(p) = found_dongle {
        eprintln!("rzcontrol: using dongle path (VID_1532&PID_009C)");
        result = Ok(p);
    } else if let Some(p) = found_ble {
        eprintln!("rzcontrol: using BLE path (VID_068E&PID_02CE)");
        result = Ok(p);
    }
    result
}
