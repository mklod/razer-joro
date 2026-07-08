; joro-lock-vm.ahk — Joro Lock key → Delete, for VMs (or any Windows box)
; that doesn't run the joro-daemon.
;
; Background: the Joro's Lock key is a firmware macro that types Win+L.
; Hypervisors feed the guest from the raw input stream, so the host
; daemon's remap can't reach a VM — this script is the in-guest fix.
;
; PREREQUISITE (one-time, in the VM, ADMIN cmd — winlogon grabs Win+L
; before any user-mode remapper unless lock-workstation is disabled):
;   reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Policies\System" /v DisableLockWorkstation /t REG_DWORD /d 1 /f
;   ...then sign out/in of the VM once.
;
; Install:
;   1. Install AutoHotkey v2 in the VM (https://www.autohotkey.com)
;   2. Double-click this file to test: pressing the Lock key should now
;      produce Delete.
;   3. Autostart: Win+R -> shell:startup -> drop a shortcut to this file.

#Requires AutoHotkey v2.0
#SingleInstance Force

; Joro Lock key (firmware Win+L) -> Delete.
; Fires on autorepeat too, so holding Lock repeats Delete like a real key.
#l::Send "{Delete}"

; Optional — Joro Copilot key in VK-macro mode arrives as Win+Shift+F23.
; Uncomment and set your preferred action if you want it in the VM too:
; #+F23::Send "^{F12}"
