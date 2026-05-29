# Capture the Synapse HyperSpeed dongle-pairing exchange.
# USBPcap on all 3 roothubs (dongle may be on any). Output:
#   L:\PROJECTS\razer-joro\captures\dongle_pair_uN.pcap
$base = 'L:\PROJECTS\razer-joro\captures'
$pcap = 'C:\Program Files\USBPcap\USBPcapCMD.exe'
1..3 | ForEach-Object {
    $i = $_
    Remove-Item "$base\dongle_pair_u$i.pcap","$base\dongle_pair_u$i.err" -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $pcap `
        -ArgumentList @('-d', "\\.\USBPcap$i", '-A', '-o', "$base\dongle_pair_u$i.pcap") `
        -WindowStyle Hidden -PassThru `
        -RedirectStandardError "$base\dongle_pair_u$i.err"
    Write-Host "USBPcap${i} started (PID $($p.Id))"
}
Start-Sleep -Milliseconds 1500
1..3 | ForEach-Object {
    $f = "$base\dongle_pair_u$_.pcap"
    if (Test-Path $f) { Write-Host "u$_ initial: $((Get-Item $f).Length) B" }
}
Write-Host "CAPTURE LIVE."
