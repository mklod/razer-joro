$base = 'L:\PROJECTS\razer-joro\captures'
$pcap = "$base\mode_toggle_u3.pcap"
$err  = "$base\mode_toggle_u3.err"
Remove-Item $pcap, $err -ErrorAction SilentlyContinue
Start-Process -FilePath 'C:\Program Files\USBPcap\USBPcapCMD.exe' `
    -ArgumentList @('-d', '\\.\USBPcap3', '-A', '-o', $pcap) `
    -WindowStyle Hidden `
    -RedirectStandardError $err
Start-Sleep -Milliseconds 1500
$procs = Get-Process USBPcapCMD -ErrorAction SilentlyContinue
Write-Host "Running processes: $($procs.Count)"
Get-ChildItem $err -ErrorAction SilentlyContinue | ForEach-Object { Get-Content $_.FullName }
Get-ChildItem $pcap -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "$($_.Name): $($_.Length) bytes" }
