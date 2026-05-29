# Starts USBPcap on all 3 roothubs simultaneously to catch a Joro firmware
# update (which may run on whichever roothub the wired keyboard is plugged into).
# Pcaps go to L:\PROJECTS\razer-joro\captures\fw_update_uN.pcap.
$base = 'L:\PROJECTS\razer-joro\captures'
$pcap = 'C:\Program Files\USBPcap\USBPcapCMD.exe'
1..3 | ForEach-Object {
    $i = $_
    Remove-Item "$base\fw_update_u$i.pcap","$base\fw_update_u$i.err" -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $pcap `
        -ArgumentList @('-d', "\\.\USBPcap$i", '-A', '-o', "$base\fw_update_u$i.pcap") `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardError "$base\fw_update_u$i.err"
    Write-Host "USBPcap${i} started (PID $($p.Id))"
}
Start-Sleep -Milliseconds 1500
1..3 | ForEach-Object {
    $f = "$base\fw_update_u$_.pcap"
    if (Test-Path $f) {
        Write-Host "USBPcap${_}.pcap initial size: $((Get-Item $f).Length) bytes"
    }
}
Write-Host "All 3 captures running. Click 'Update Firmware' in Synapse now."
