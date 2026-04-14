$base = 'L:\PROJECTS\razer-joro\captures'
1..3 | ForEach-Object {
    $i = $_
    $dev = "\\.\USBPcap$i"
    $pcap = "$base\daemon_write_u$i.pcap"
    $err = "$base\daemon_write_u$i.err"
    Start-Process -FilePath 'C:\Program Files\USBPcap\USBPcapCMD.exe' `
        -ArgumentList @('-d', $dev, '-A', '-o', $pcap) `
        -WindowStyle Hidden `
        -RedirectStandardError $err
}
Start-Sleep -Milliseconds 1000
Write-Host "USBPcapCMD running: $((Get-Process USBPcapCMD -ErrorAction SilentlyContinue).Count)"
