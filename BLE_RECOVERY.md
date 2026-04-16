# Joro BLE Recovery Runbook

Quick reference for when the Joro stops working over BLE and the daemon can't connect. Covers the typical failure modes we've hit in this project, root causes, and the exact commands to fix them.

## Why this document exists

BLE is notoriously fragile on Windows. The Joro setup has three moving parts that can all get stuck:

1. **The BLE dongle** (BARROT Bluetooth 5.4 Adapter, VID `0x33FA` PID `0x0010`) — external USB dongle that acts as the BT radio.
2. **Windows' "one active BT adapter" rule** — Windows only allows ONE Bluetooth radio bound at a time. Your system has two candidates: the external BARROT dongle and the **Intel Wireless Bluetooth** onboard (VID `0x8087` PID `0x0AA7`). If both enumerate, whichever one wins the race claims the slot and the loser gets `CM_PROB_FAILED_ADD`.
3. **The Joro itself** — shows as `BTHLE\DEV_C8E2775D2FA4\...`. Without a working BT radio to enumerate through, Joro appears as `Status: Unknown` with stale cached GATT service children.

When any of those three is broken, the daemon can't reach the keyboard.

---

## Symptom matrix

| Symptom | Likely cause | Go to |
|---|---|---|
| `cargo run` exits with `find_paired_joro` returning `None` | BARROT in Error state | §1 BARROT recovery |
| Daemon log shows `no paired BLE device found` | BARROT in Error OR Intel BT won the race | §1 BARROT recovery |
| `cargo run` connects but GATT writes fail with `object closed` | Stale pairing, re-pair needed | §3 Re-pair Joro |
| Joro appears `Unknown` in `Get-PnpDevice`, BARROT appears `Error` | Classic post-power-outage state | §1 BARROT recovery |
| Everything looks `OK` in PnP but daemon can't connect | Running Razer `RzBTLEManager` holding the GATT session | §2 Kill Razer services |
| After Windows Update, Intel BT silently re-enabled | Windows Update re-installed the Intel driver | §1.4 permanent fix |

---

## §1. BARROT recovery (the usual post-reboot fix)

### 1.1 — Check state

```powershell
Get-PnpDevice -Class Bluetooth |
  Where-Object { $_.FriendlyName -match 'BARROT|Intel|Joro' -and $_.Status -notmatch 'Unknown' } |
  Select-Object Status,FriendlyName,InstanceId | Format-Table -AutoSize
```

You're looking at two questions:
- Is **Intel(R) Wireless Bluetooth(R)** Status `OK`? → it's hogging the BT slot; go to §1.3.
- Is **BARROT Bluetooth 5.4 Adapter** Status `Error`? → disable Intel first (§1.3), then check BARROT again.

Full problem code:
```powershell
Get-PnpDevice -InstanceId 'USB\VID_33FA&PID_0010\...' | Select-Object Status,Problem,ProblemDescription | Format-List
```

`CM_PROB_FAILED_ADD` → driver load failed, almost always because Intel BT is active. Check the System event log:
```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; StartTime=(Get-Date).AddMinutes(-30)} |
  Where-Object { $_.ProviderName -match 'BTHUSB|Kernel-PnP' } |
  Select-Object -First 10 TimeCreated,Id,LevelDisplayName,Message | Format-List
```

Look for: **`BTHUSB: Only one active Bluetooth adapter is supported at a time.`** That's the smoking gun.

### 1.2 — Use the right quoting for pnputil

Device instance IDs contain `&` which bash, cmd, AND pnputil all handle differently. **Always use PowerShell with single quotes** — it's the only form that survives without mangling:

```powershell
pnputil /disable-device 'USB\VID_8087&PID_0AA7\6&363B4CA8&0&10'
```

Via `powershell -NoProfile -Command '...'` from bash also works because bash leaves the single-quoted content intact for PowerShell to parse.

Do **not** use:
- `cmd /c pnputil ...` — cmd tries to escape `&` and fails or includes the escape char literally.
- Bash with double quotes — bash eats unescaped `&`.

### 1.3 — Disable Intel onboard BT (the fix)

```powershell
pnputil /disable-device 'USB\VID_8087&PID_0AA7\6&363B4CA8&0&10'
```

Then trigger a rescan so Windows retries the BARROT driver bind:
```powershell
pnputil /scan-devices
```

If BARROT is still `Error` after 5 seconds, cycle it too:
```powershell
pnputil /disable-device 'USB\VID_33FA&PID_0010\6&363B4CA8&0&5'
Start-Sleep -Seconds 2
pnputil /enable-device 'USB\VID_33FA&PID_0010\6&363B4CA8&0&5'
```

If that still fails, physically unplug the BARROT dongle, wait 3–5 seconds, plug it back in. (A different USB port is fine — Windows will reuse the same driver.)

Verify:
```powershell
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'BARROT|Joro' } |
  Select-Object Status,FriendlyName | Format-Table -AutoSize
```

Once BARROT is `OK` and Joro's GATT service tree (Device Info, Generic Access, Generic Attribute, Battery, and `52401523-F97C-7F90-0E7F-6C6F4E36DB1C` Razer custom service) all read `OK`, the daemon can connect.

### 1.4 — Permanent fix for the "Intel BT keeps coming back" problem

Disabling Intel BT via Device Manager is **not permanent**. Three things can re-enable it:

1. **Windows Update** reinstalling the Intel Wireless driver package.
2. **Major OS updates** resetting `DEVPKEY_Device_IsDisabled`.
3. **Cold boot after a power outage** — PnP cache gets cleared and Windows re-adds the device from the driver store with `Enabled` as default.

Options from easiest to most aggressive. **All three of options 1–3 are currently active** on this system (belt + suspenders + Group Policy):

1. **BIOS/UEFI disable** — reboot into BIOS, find the "Advanced / Onboard Devices / Wireless" or "Integrated Peripherals > Bluetooth" toggle, and disable **Bluetooth** (keep Wi-Fi if you want it). BT and Wi-Fi on Intel cards are the same chip but separate radios, so the BT toggle doesn't break Wi-Fi. **This system's BIOS (Ryzen board) does NOT expose a separate BT toggle** — disabling the WAN antenna didn't help. So we rely on options 2 + 3.

2. **Scheduled task at boot** — `C:\Tools\disable-intel-bt.ps1` runs at every boot as SYSTEM via `schtasks /SC ONSTART`. Waits 5s for USB enumeration to settle, disables Intel BT via `pnputil /disable-device`, then cycles BARROT (disable → enable) so it picks up the freed BT slot. Logs to `C:\Tools\disable-intel-bt.log`.

   The script (`C:\Tools\disable-intel-bt.ps1`):
   ```powershell
   $intel = 'USB\VID_8087&PID_0AA7\6&363B4CA8&0&10'
   $barrot = 'USB\VID_33FA&PID_0010\6&363B4CA8&0&5'
   Start-Sleep 5
   $dev = Get-PnpDevice -InstanceId $intel -ErrorAction SilentlyContinue
   if ($dev -and $dev.Status -eq 'OK') {
       pnputil /disable-device $intel | Out-Null
       Start-Sleep 2
       pnputil /disable-device $barrot 2>$null | Out-Null
       Start-Sleep 2
       pnputil /enable-device $barrot 2>$null | Out-Null
   }
   ```

   Registered via `schtasks.exe` (NOT `Register-ScheduledTask` PowerShell cmdlet — the latter silently drops SYSTEM AtStartup triggers when run non-elevated):
   ```
   schtasks /Create /TN "DisableIntelBT" /TR "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File C:\Tools\disable-intel-bt.ps1" /SC ONSTART /RU SYSTEM /RL HIGHEST /F
   ```

3. **Group Policy driver installation block** ← **THE NUCLEAR OPTION, CURRENTLY ACTIVE.** Tells Windows to NEVER load any driver for `USB\VID_8087` (all Intel Bluetooth hardware). The device still APPEARS in Device Manager (the hardware is physically on the motherboard) but its Status is permanently `Error` because Windows refuses to bind a driver. Survives Windows Updates, reboots, driver reinstalls — everything. The Intel BT adapter is a zombie: visible but non-functional.

   Set via registry:
   ```powershell
   $basePath = 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions'
   $denyPath = "$basePath\DenyDeviceIDs"
   New-Item -Path $basePath -Force | Out-Null
   New-Item -Path $denyPath -Force | Out-Null
   Set-ItemProperty -Path $basePath -Name 'DenyDeviceIDs' -Value 1 -Type DWord
   Set-ItemProperty -Path $denyPath -Name '1' -Value 'USB\VID_8087&PID_0AA7' -Type String
   Set-ItemProperty -Path $denyPath -Name '2' -Value 'USB\VID_8087' -Type String
   ```

   **To reverse** (if you ever need Intel BT back):
   ```powershell
   Remove-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DeviceInstall\Restrictions' -Recurse
   ```

   **Verified 2026-04-16:** after reboot with GP block active, Intel BT shows `Status: Error` (driver blocked), BARROT shows `Status: OK`, Joro connects at the Windows login screen, daemon starts silently via HKCU Run key. Full success.

4. **Last resort: physical hardware mod.** The Intel Wireless card is soldered to the motherboard on this system. Desoldering or cutting the BT antenna trace would permanently disable it. Only consider if all software approaches fail AND the BARROT dongle becomes the primary reliability bottleneck.

**Daemon-side defense (in addition to the above):**

5. **GATT health watchdog (built into daemon 2026-04-16).** The daemon's `poll_battery` (every 30s) tracks consecutive GATT read failures. After 3 failures, it force-disconnects the stale BLE session, triggering a full reconnect cycle including `fn_detect::reset()` (fresh HID handles). This catches the case where the BLE adapter is externally cycled and the daemon's WinRT `BluetoothLEDevice` still reports `ConnectionStatus::Connected` on a dead GATT session. Without this, Hypershift and brightness silently stop working after any BLE hiccup until the daemon is manually restarted.

---

## §2. Kill Razer services (the "my daemon can't get a GATT session" case)

Razer's Chroma SDK ships a family of subservices that can hold GATT sessions to the Joro, fighting our daemon. After `cargo run -- set-mode mm` or `fn` succeeds but subsequent connects fail, suspect these.

Running Razer services to check:
```powershell
Get-Service | Where-Object { $_.Name -match 'Rz|Razer' -and $_.Status -eq 'Running' } | Format-Table -AutoSize
```

Expected to see at least:
- **Razer Chroma SDK Server** → owns `RzBTLEManager`, `RzDeviceManager`, etc.
- **Razer Chroma SDK Service** → Chroma state
- **Razer Elevation Service** → keep this running; Copilot key remaps depend on it

Kill the Chroma SDK stack (admin required):
```powershell
Stop-Service -Name 'Razer Chroma SDK Server' -Force
Stop-Service -Name 'Razer Chroma SDK Service' -Force
```

This cascades and kills:
- `RzBTLEManager.exe` (the one that actually grabs GATT sessions)
- `RzDeviceManager*`, `RzChromaConnect*`, `RzIoTDeviceManager`, `RzSmartlightingDeviceManager`, `RzWDLDeviceManager`, `RzAppManager`

You can also kill `RazerAppEngine.exe` (the Synapse Electron app) if it's running:
```powershell
taskkill /F /IM RazerAppEngine.exe /T
```

Keep `razer_elevation_service` alive unless you're specifically debugging it — the Copilot key trigger remap needs the combo it emits.

---

## §3. Re-pair Joro (the "object closed" / stale pairing case)

If the daemon connects but GATT writes fail with `object closed` or `Access is denied`, the Windows-side pairing record has drifted out of sync with the keyboard. The only reliable fix is to re-pair:

1. Windows Settings → Bluetooth → find **Joro** → Remove device.
2. Power-cycle the Joro (slide switch off → wait 3s → on).
3. Settings → Add device → Bluetooth → wait for **Joro** → pair.
4. Relaunch daemon.

Notes:
- Don't try to use the paired Joro while Razer Synapse is running. Synapse sometimes grabs the BT connection first and the daemon then sees a "half-paired" state.
- If Windows says "Pairing failed", it's usually because Synapse's BLE proxy intercepted the handshake. Kill `RazerAppEngine.exe` first (§2), then retry.

See memory note `project_joro_pairing_requirement.md` for the full backstory.

---

## §4. Full nuclear recovery (everything above failed)

Sequential, escalating. Do 4.1, test, 4.2, test, etc.

### 4.1 — Reboot Windows

Obvious but effective for PnP-cache-related issues. After reboot, immediately check §1.1.

### 4.2 — Reboot + physical dongle swap

Unplug the BARROT before reboot. Boot to desktop. Plug BARROT back in (any USB port). Windows re-enumerates the dongle as a fresh device. Intel BT may still win the race, so have the §1.3 disable ready.

### 4.3 — Disable Intel BT in BIOS

Described in §1.4 option 1. Single most effective permanent fix.

### 4.4 — Clear driver cache + reinstall BARROT driver

```powershell
pnputil /delete-driver oem4.inf /uninstall /force
```

Then unplug, plug back in. Windows re-downloads the BARROT driver from Windows Update, or falls back to the inbox generic Bluetooth driver.

### 4.5 — Clear Windows Bluetooth pairing database

```powershell
Stop-Service bthserv -Force
```

```text
Delete registry key:
HKLM\SYSTEM\CurrentControlSet\Services\BTHPORT\Parameters\Keys
```

(Back it up first: `reg export ... backup.reg`.) This wipes ALL Bluetooth pairings — you'll need to re-pair everything including mice, headphones, etc.

Restart `bthserv`. Re-pair Joro via Windows Settings.

---

## §5. Diagnostic one-liners

Full Joro health check:
```powershell
Get-PnpDevice | Where-Object { $_.FriendlyName -match 'BARROT|Joro|Intel.*Bluetooth' } |
  Select-Object Status,FriendlyName,InstanceId | Format-Table -Wrap -AutoSize
```

Check for the "only one BT adapter" event:
```powershell
Get-WinEvent -FilterHashtable @{LogName='System'; ProviderName='BTHUSB'} -MaxEvents 20 |
  Select-Object TimeCreated,Id,Message | Format-List
```

Which driver is bound to BARROT:
```powershell
Get-PnpDeviceProperty -InstanceId 'USB\VID_33FA&PID_0010\6&363B4CA8&0&5' `
  -KeyName 'DEVPKEY_Device_DriverInfPath','DEVPKEY_Device_DriverVersion','DEVPKEY_Device_DriverProvider'
```

Daemon one-shot connect test (runs, sets mode, exits):
```
cargo run -- set-mode fn
```

If `set-mode` prints `current = Fn` and exits cleanly, the full BLE GATT path is alive.

---

## §6. Known causes we've hit

| Date | Cause | Fix | Memory |
|---|---|---|---|
| 2026-04-16 | Intel BT re-enabled after reboot despite Device Manager uninstall | Group Policy driver block (`DenyDeviceIDs` for `USB\VID_8087`) + scheduled task + BARROT cycle. BIOS has no BT toggle. | §1.4 option 3 |
| 2026-04-16 | Hypershift/brightness silently broken after external BLE adapter cycle | GATT health watchdog in daemon (`poll_battery` → 3 failures → force disconnect → reconnect with fn_detect::reset). Daemon no longer needs manual restart. | §1.4 option 5 |
| 2026-04-15 | Post-power-outage cold boot, Intel BT won the PnP race | Disable Intel BT via pnputil | §1.3 |
| 2026-04-15 | fn_detect HID handles stale after BLE disconnect/reconnect | `fn_detect::reset()` called from `try_connect` on every BLE reconnect | §1.4 option 5 |
| 2026-04-14 | Stale pairing after Synapse killed mid-write | Re-pair Joro | §3, `project_joro_pairing_requirement.md` |
| 2026-04-13 | btleplug on Windows dropped GATT session after ~1s | Switched to direct WinRT in `src/ble.rs` | `project_btleplug_winrt_fix.md` |
| 2026-04-10 | Synapse Razer Chroma SDK holding Joro GATT session | Stop Chroma SDK services | §2 |
