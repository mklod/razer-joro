// src/fn_detect_rawinput.rs — Win32 RawInput Fn-state detection for Joro dongle
//
// On the BLE transport, fn_detect.rs reads vendor HID `05 04 state`
// reports via hidapi to track Fn-held state. Through the dongle (PID 0x009C),
// that vendor report is dropped at the dongle bridge and never reaches
// userland HID — but the consumer-page Fn signal does still cross the wire.
//
// **Signal:** the dongle delivers consumer report ID 0x02 with usage 0x029D
// (AC View Toggle) on every Fn press, and report 0x02 with usage 0x0000 on
// release. Verified via USBPcap + RawInput on 2026-04-25:
//
//     type=HID  02 9d 02 00 ...   <- Fn DOWN
//     type=HID  02 00 00 00 ...   <- Fn UP
//
// Standard `hidapi::read()` doesn't see report 0x02 because the consumer
// collection delivering it is owned exclusively by Windows kbdhid. RawInput
// hooks lower in the HID stack and DOES see it.
//
// This module spawns a hidden top-level window in a background thread,
// subscribes to the consumer usage page (0x000C / 0x0001) with
// RIDEV_INPUTSINK, and updates `fn_detect::FN_HELD` on every Fn signal.
// Idempotent: safe to call `start()` repeatedly.

use std::ffi::c_void;
use std::mem::{size_of, zeroed};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{HANDLE, HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::UI::Input::{
    GetRawInputData, GetRawInputDeviceInfoW, RegisterRawInputDevices,
    HRAWINPUT, RAWINPUTDEVICE, RAWINPUTHEADER,
    RIDEV_INPUTSINK, RIDEV_PAGEONLY, RID_INPUT, RIDI_DEVICENAME,
    RIM_TYPEHID,
};
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW,
    RegisterClassW, TranslateMessage,
    HMENU, MSG, WINDOW_EX_STYLE, WM_INPUT, WNDCLASSW, WS_OVERLAPPED,
};

static STARTED: AtomicBool = AtomicBool::new(false);

/// Spawn the RawInput Fn-detect thread once. Subsequent calls are no-ops.
pub fn start() {
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    thread::spawn(move || {
        if let Err(e) = run() {
            eprintln!("fn-detect-rawinput: thread exited: {e}");
            STARTED.store(false, Ordering::SeqCst);
        }
    });
}

fn run() -> Result<(), String> {
    unsafe {
        let hinst = GetModuleHandleW(None).map_err(|e| format!("GetModuleHandle: {e}"))?;
        let class_name: Vec<u16> = "JoroFnDetectRawInput\0".encode_utf16().collect();

        let mut wc: WNDCLASSW = zeroed();
        wc.lpfnWndProc = Some(wnd_proc);
        wc.hInstance = hinst.into();
        wc.lpszClassName = PCWSTR(class_name.as_ptr());

        let atom = RegisterClassW(&wc);
        if atom == 0 {
            return Err(format!("RegisterClassW failed: {}", windows::core::Error::from_win32()));
        }

        let title: Vec<u16> = "JoroFnDetectRawInput\0".encode_utf16().collect();
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            PCWSTR(class_name.as_ptr()),
            PCWSTR(title.as_ptr()),
            WS_OVERLAPPED,
            0, 0, 0, 0,
            HWND(std::ptr::null_mut()),
            HMENU(std::ptr::null_mut()),
            hinst,
            None,
        ).map_err(|e| format!("CreateWindowEx: {e}"))?;

        // Consumer-page subscription (0x000C/0x0001) drops keystrokes under
        // load — confirmed via A/B test 2026-05-01. The system-wide INPUTSINK
        // on consumer page interferes with kbdhid's keyboard routing.
        //
        // Vendor page only. Joro emits a vendor report 0x08 byte 1 = Fn state
        // alongside the consumer 0x029D (USBPcap captured both). If that
        // report routes to vendor usage page 0xFF00 we'll catch it here.
        let rids = [
            RAWINPUTDEVICE {
                usUsagePage: 0xFF00,
                usUsage: 0x0000,
                dwFlags: RIDEV_INPUTSINK | RIDEV_PAGEONLY,
                hwndTarget: hwnd,
            },
        ];
        RegisterRawInputDevices(&rids, size_of::<RAWINPUTDEVICE>() as u32)
            .map_err(|e| format!("RegisterRawInputDevices: {e}"))?;
        eprintln!("fn-detect-rawinput: subscribed to consumer + vendor HID pages");

        // Standard message pump
        let mut msg: MSG = zeroed();
        loop {
            let r = GetMessageW(&mut msg, HWND(std::ptr::null_mut()), 0, 0);
            if r.0 == 0 || r.0 == -1 {
                break;
            }
            let _ = TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }
    Ok(())
}

unsafe extern "system" fn wnd_proc(
    hwnd: HWND, msg: u32, wparam: WPARAM, lparam: LPARAM,
) -> LRESULT {
    if msg == WM_INPUT {
        handle_raw_input(HRAWINPUT(lparam.0 as *mut _));
    }
    DefWindowProcW(hwnd, msg, wparam, lparam)
}

unsafe fn handle_raw_input(hri: HRAWINPUT) {
    // Two-call pattern: first to get size, second to get data.
    let mut size: u32 = 0;
    GetRawInputData(
        hri, RID_INPUT, None, &mut size,
        size_of::<RAWINPUTHEADER>() as u32,
    );
    if size == 0 {
        return;
    }
    let mut buf: Vec<u8> = vec![0; size as usize];
    let n = GetRawInputData(
        hri, RID_INPUT, Some(buf.as_mut_ptr() as *mut c_void),
        &mut size, size_of::<RAWINPUTHEADER>() as u32,
    );
    if n == u32::MAX || n == 0 {
        return;
    }

    // Header
    let hdr_ptr = buf.as_ptr() as *const RAWINPUTHEADER;
    let hdr = &*hdr_ptr;
    if hdr.dwType != RIM_TYPEHID.0 {
        return;
    }

    // Device-name filter — only act on Joro dongle (VID_1532 PID_009C).
    if !is_joro_dongle(hdr.hDevice) {
        return;
    }

    // RAWHID is at offset sizeof(RAWINPUTHEADER). Layout:
    //     u32 dwSizeHid
    //     u32 dwCount
    //     u8  bRawData[dwSizeHid * dwCount]
    let header_size = size_of::<RAWINPUTHEADER>();
    if buf.len() < header_size + 8 {
        return;
    }
    let size_hid = u32::from_le_bytes(
        buf[header_size..header_size + 4].try_into().unwrap_or([0; 4])
    ) as usize;
    let count = u32::from_le_bytes(
        buf[header_size + 4..header_size + 8].try_into().unwrap_or([0; 4])
    ) as usize;
    if count == 0 || size_hid < 3 {
        return;
    }
    let data_start = header_size + 8;
    if buf.len() < data_start + size_hid {
        return;
    }
    let report = &buf[data_start..data_start + size_hid];

    // Diagnostic: log every Joro vendor-page WM_INPUT so we can identify
    // which report ID carries Fn-state. Per USBPcap (2026-04-27), report
    // 0x08 byte 1 = Fn state. If this report arrives via vendor page
    // (0xFF00), we'll see it logged here and can wire it up.
    let dump: String = report.iter().take(16).map(|b| format!("{:02x}", b)).collect::<Vec<_>>().join(" ");
    eprintln!("fn-detect-rawinput: vendor rid=0x{:02x} ({}B) [{}]", report[0], size_hid, dump);

    // Report 0x08: Joro Fn-state vendor report. Byte 1 = state (0x01=down, 0x00=up).
    if report[0] == 0x08 && report.len() >= 2 {
        let held_now = report[1] == 0x01;
        let prev = crate::fn_detect::FN_HELD.swap(held_now, Ordering::Release);
        if prev != held_now {
            eprintln!(
                "fn-detect-rawinput: FN_HELD {} -> {} (vendor rid=0x08 byte1=0x{:02x})",
                prev, held_now, report[1]
            );
        }
        return;
    }

    // Legacy consumer 0x02 + usage 0x029D path — only fires if the consumer
    // page subscription is re-enabled (currently OFF due to dropped-keys).
    if report[0] != 0x02 || report.len() < 3 {
        return;
    }
    let usage = u16::from_le_bytes([report[1], report[2]]);
    let held_now = match usage {
        0x029D => true,
        0x0000 => false,
        _ => return,
    };
    let prev = crate::fn_detect::FN_HELD.swap(held_now, Ordering::Release);
    if prev != held_now {
        eprintln!(
            "fn-detect-rawinput: FN_HELD {} -> {} (consumer 0x{:04X})",
            prev, held_now, usage
        );
    }
}

unsafe fn is_joro_dongle(hdevice: HANDLE) -> bool {
    let mut size: u32 = 0;
    GetRawInputDeviceInfoW(hdevice, RIDI_DEVICENAME, None, &mut size);
    if size == 0 || size > 4096 {
        return false;
    }
    let mut buf: Vec<u16> = vec![0; size as usize];
    let n = GetRawInputDeviceInfoW(
        hdevice, RIDI_DEVICENAME,
        Some(buf.as_mut_ptr() as *mut c_void),
        &mut size,
    );
    if n == u32::MAX || n == 0 {
        return false;
    }
    let name = String::from_utf16_lossy(&buf[..n as usize]).to_ascii_lowercase();
    name.contains("vid_1532") && name.contains("pid_009c")
}
