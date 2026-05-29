# Capture Synapse's COMPLETE dongle-setup + Joro-pair workflow on the
# Razer HyperSpeed dongle (PID 0x009C). The mouse is plugged in to
# satisfy Synapse's anchor-device requirement — but the mouse only
# serves as the anchor; we ignore mouse-specific Protocol30 frames in
# analysis. Goal: extract the dongle commands Synapse sends to (a)
# enter pair mode, (b) accept the keyboard, (c) any post-pair init —
# so an OSS replay tool can do the same WITHOUT the mouse.
#
# USBPcap on all 3 roothubs; the dongle may be on any. Stop with the
# kill script. Run BEFORE opening Synapse so we catch startup traffic.
$base = 'L:\PROJECTS\razer-joro\captures'
$pcap = 'C:\Program Files\USBPcap\USBPcapCMD.exe'
1..3 | ForEach-Object {
    $i = $_
    Remove-Item "$base\dongle_setup_full_u$i.pcap","$base\dongle_setup_full_u$i.err" -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $pcap `
        -ArgumentList @('-d', "\\.\USBPcap$i", '-A', '-o', "$base\dongle_setup_full_u$i.pcap") `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardError "$base\dongle_setup_full_u$i.err"
    Write-Host "USBPcap${i} started (PID $($p.Id))"
}
Start-Sleep -Milliseconds 1500
1..3 | ForEach-Object {
    $f = "$base\dongle_setup_full_u$_.pcap"
    if (Test-Path $f) { Write-Host "u$_ initial: $((Get-Item $f).Length) B" }
}
Write-Host "CAPTURE LIVE - now do the dongle setup + Joro pair."
