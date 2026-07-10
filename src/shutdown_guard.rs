// src/shutdown_guard.rs — clean GATT release on system shutdown/reboot
// Last modified: 2026-07-09
//
// The daemon holds a WinRT GattSession (MaintainConnection=true) on the
// Joro. If the process dies without dropping it, the session leaks and
// the keyboard is left holding a zombie connection — after the next boot
// it won't resume bonded advertising and the user has to unpair/re-pair
// in Windows (the ctrlc comment in main.rs and BLE_RECOVERY.md both
// record this failure mode). Tray-Quit and Ctrl+C already run
// shutdown_and_exit(), which drops the BleDevice cleanly so the keyboard
// resumes advertising. System shutdown/reboot/logoff did NOT go through
// that path: Windows sends WM_ENDSESSION and then kills the process.
//
// This module spawns a hidden top-level window (NOT message-only — those
// never receive broadcast messages) that listens for WM_ENDSESSION and
// posts UserEvent::SystemShutdown to the main event loop, then parks its
// own thread: returning from WM_ENDSESSION tells Windows it may terminate
// us at any moment, so we stay inside the handler until
// shutdown_and_exit()'s process::exit ends the process (4 s cap keeps us
// under the hung-app timeout if the main loop is wedged).
//
// SetProcessShutdownParameters(0x3FF) asks Windows to end us as early as
// possible in the shutdown order, while the BT stack is still alive to
// deliver the disconnect to the keyboard.

use std::mem::zeroed;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::Duration;

use windows::core::PCWSTR;
use windows::Win32::Foundation::{HWND, LPARAM, LRESULT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::SetProcessShutdownParameters;
use windows::Win32::UI::WindowsAndMessaging::{
    CreateWindowExW, DefWindowProcW, DispatchMessageW, GetMessageW, RegisterClassW,
    TranslateMessage, HMENU, MSG, WINDOW_EX_STYLE, WM_ENDSESSION, WM_QUERYENDSESSION, WNDCLASSW,
    WS_OVERLAPPED,
};

static STARTED: AtomicBool = AtomicBool::new(false);

/// Spawn the shutdown-guard thread once. Subsequent calls are no-ops.
pub fn start() {
    if STARTED.swap(true, Ordering::SeqCst) {
        return;
    }
    thread::spawn(|| {
        if let Err(e) = run() {
            eprintln!("shutdown-guard: thread exited: {e}");
            STARTED.store(false, Ordering::SeqCst);
        }
    });
}

fn run() -> Result<(), String> {
    unsafe {
        // App shutdown priority range is 0x100-0x3FF, higher = earlier
        // (default 0x280). Go first so the GATT drop happens while the
        // Bluetooth stack can still deliver the disconnect.
        if let Err(e) = SetProcessShutdownParameters(0x3FF, 0) {
            eprintln!("shutdown-guard: SetProcessShutdownParameters failed: {e}");
        }

        let hinst = GetModuleHandleW(None).map_err(|e| format!("GetModuleHandle: {e}"))?;
        let class_name: Vec<u16> = "JoroShutdownGuard\0".encode_utf16().collect();

        let mut wc: WNDCLASSW = zeroed();
        wc.lpfnWndProc = Some(wnd_proc);
        wc.hInstance = hinst.into();
        wc.lpszClassName = PCWSTR(class_name.as_ptr());

        let atom = RegisterClassW(&wc);
        if atom == 0 {
            return Err(format!(
                "RegisterClassW failed: {}",
                windows::core::Error::from_win32()
            ));
        }

        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            PCWSTR(class_name.as_ptr()),
            PCWSTR(class_name.as_ptr()),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            HWND(std::ptr::null_mut()),
            HMENU(std::ptr::null_mut()),
            hinst,
            None,
        )
        .map_err(|e| format!("CreateWindowEx: {e}"))?;
        let _ = hwnd;
        eprintln!("shutdown-guard: armed (WM_ENDSESSION -> clean BLE drop)");

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
    hwnd: HWND,
    msg: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match msg {
        // Cancelable phase — say "fine by us" but don't clean up yet;
        // another app may veto the shutdown.
        WM_QUERYENDSESSION => LRESULT(1),
        // The session IS ending (wparam != 0). Trigger the same graceful
        // path as tray-Quit, then park: once this handler returns, Windows
        // may terminate the process before the BLE drop completes.
        WM_ENDSESSION if wparam.0 != 0 => {
            eprintln!("shutdown-guard: WM_ENDSESSION — releasing keyboard before the OS kills us");
            crate::post_user_event(crate::UserEvent::SystemShutdown);
            for _ in 0..80 {
                thread::sleep(Duration::from_millis(50));
            }
            eprintln!("shutdown-guard: main loop never exited within 4s — letting Windows kill us");
            LRESULT(0)
        }
        _ => DefWindowProcW(hwnd, msg, wparam, lparam),
    }
}
