# salvage-bt-dirty-boot.ps1 -- restore Joro BLE reconnect after a dirty shutdown
# Runs at every boot as SYSTEM (scheduled task SalvageBTDirtyBoot, ONSTART).
#
# Why: a power-loss / hard-reset shutdown skips WM_ENDSESSION, so the
# joro-daemon's shutdown guard never releases its GATT session. After such
# a boot the Bluetooth stack won't reconnect the bonded Joro (login screen
# dead); the manual fix was replugging the BARROT USB adapter or re-pairing.
# Restarting the adapter re-initializes the stack the same way a replug does.
#
# Windows logs Kernel-Power event 41 during the boot that FOLLOWS a dirty
# shutdown, so its presence in the last few minutes identifies exactly the
# boots that need salvage. Clean boots: no event -> no action.
#
# NOTE: ASCII only -- Windows PowerShell 5.1 parses BOM-less UTF-8 as ANSI.
# See razer-joro _status.md 2026-07-14 + BLE_RECOVERY.md section 1.4.

$log = 'C:\Tools\salvage-bt-dirty-boot.log'
Start-Sleep 10   # let USB/BT enumeration settle before judging or cycling

$ev = Get-WinEvent -FilterHashtable @{
    LogName = 'System'
    ProviderName = 'Microsoft-Windows-Kernel-Power'
    Id = 41
    StartTime = (Get-Date).AddMinutes(-10)
} -MaxEvents 1 -ErrorAction SilentlyContinue

if (-not $ev) {
    Add-Content $log "$(Get-Date -Format s) clean boot - no action"
    exit 0
}

# Find the live BARROT adapter dynamically -- its instance path changes if it
# is ever moved to another USB port (a stale ghost entry stays behind).
$barrot = Get-PnpDevice | Where-Object {
    $_.InstanceId -like 'USB\VID_33FA&PID_0010*' -and $_.Status -eq 'OK'
} | Select-Object -First 1

if (-not $barrot) {
    Add-Content $log "$(Get-Date -Format s) dirty boot detected but no live BARROT adapter found - nothing to cycle"
    exit 1
}

pnputil /restart-device "$($barrot.InstanceId)" | Out-Null
Add-Content $log "$(Get-Date -Format s) dirty boot detected (Kernel-Power 41 at $($ev.TimeCreated.ToString('s'))) - restarted BARROT $($barrot.InstanceId) exit=$LASTEXITCODE"
