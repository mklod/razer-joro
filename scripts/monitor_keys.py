"""
Use Windows WH_KEYBOARD_LL low-level hook to capture every key event
the OS sees. Prints VK code, scancode, and modifiers. Useful for
determining whether a key (F8 = consumer BrightnessDown, Fn+Left, etc.)
even reaches Windows at all when typed through the dongle.
"""
import ctypes
from ctypes import wintypes
import time

WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105

user32 = ctypes.WinDLL('user32', use_last_error=True)
kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

LowLevelKeyboardProc = ctypes.WINFUNCTYPE(
    wintypes.LPARAM, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ('vkCode', wintypes.DWORD),
        ('scanCode', wintypes.DWORD),
        ('flags', wintypes.DWORD),
        ('time', wintypes.DWORD),
        ('dwExtraInfo', ctypes.c_void_p),
    ]

VK_NAMES = {
    0x70: 'F1', 0x71: 'F2', 0x72: 'F3', 0x73: 'F4', 0x74: 'F5',
    0x75: 'F6', 0x76: 'F7', 0x77: 'F8', 0x78: 'F9', 0x79: 'F10',
    0x7A: 'F11', 0x7B: 'F12', 0x7C: 'F13', 0x7D: 'F14', 0x7E: 'F15',
    0x7F: 'F16', 0x80: 'F17', 0x81: 'F18', 0x82: 'F19', 0x83: 'F20',
    0x84: 'F21', 0x85: 'F22', 0x86: 'F23', 0x87: 'F24',
    0x25: 'LEFT', 0x26: 'UP', 0x27: 'RIGHT', 0x28: 'DOWN',
    0x2C: 'PRINTSCREEN',
    0x4C: 'L', 0x41: 'A',
    0x10: 'SHIFT', 0x11: 'CTRL', 0x12: 'ALT',
    0xA0: 'LSHIFT', 0xA1: 'RSHIFT',
    0xA2: 'LCTRL', 0xA3: 'RCTRL',
    0xA4: 'LMENU', 0xA5: 'RMENU',
    0x5B: 'LWIN', 0x5C: 'RWIN',
    0xAD: 'VOLUME_MUTE', 0xAE: 'VOLUME_DOWN', 0xAF: 'VOLUME_UP',
    0xB0: 'MEDIA_NEXT', 0xB1: 'MEDIA_PREV', 0xB2: 'MEDIA_STOP', 0xB3: 'MEDIA_PLAY',
    0xFF: 'NONE',
}

def vk_name(vk):
    return VK_NAMES.get(vk, f'VK_0x{vk:02X}')

def low_level_keyboard_proc(nCode, wParam, lParam):
    if nCode >= 0:
        kb = ctypes.cast(lParam, ctypes.POINTER(KBDLLHOOKSTRUCT))[0]
        msg_name = {
            WM_KEYDOWN: 'DOWN ', WM_KEYUP: 'UP   ',
            WM_SYSKEYDOWN: 'SDOWN', WM_SYSKEYUP: 'SUP  '
        }.get(wParam, f'?{wParam:#x}')
        ts = time.strftime("%H:%M:%S")
        ext = ' EXT' if kb.flags & 0x01 else ''
        inj = ' INJ' if kb.flags & 0x10 else ''
        print(f"{ts}  {msg_name}  vk=0x{kb.vkCode:02X} ({vk_name(kb.vkCode)})  sc=0x{kb.scanCode:02X}{ext}{inj}", flush=True)
    return user32.CallNextHookEx(None, nCode, wParam, lParam)

LP_MSG = ctypes.POINTER(wintypes.MSG)

def main():
    proc = LowLevelKeyboardProc(low_level_keyboard_proc)
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    hmod = kernel32.GetModuleHandleW(None)
    user32.SetWindowsHookExW.restype = wintypes.HHOOK
    user32.SetWindowsHookExW.argtypes = [ctypes.c_int, LowLevelKeyboardProc, wintypes.HMODULE, wintypes.DWORD]
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc, hmod, 0)
    if not hook:
        err = ctypes.get_last_error()
        print(f"SetWindowsHookEx failed: {err}")
        return

    print("Hook installed. Press any keys. Ctrl+C to stop.")
    msg = wintypes.MSG()
    try:
        while True:
            ret = user32.GetMessageA(ctypes.byref(msg), None, 0, 0)
            if ret == 0 or ret == -1:
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageA(ctypes.byref(msg))
    except KeyboardInterrupt:
        pass
    finally:
        user32.UnhookWindowsHookEx(hook)

if __name__ == "__main__":
    main()
