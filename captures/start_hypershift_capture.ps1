$base = 'L:\PROJECTS\razer-joro\captures'
1..3 | ForEach-Object {
    $i = $_
    $dev = "\\.\USBPcap$i"
    $pcap = "$base\synapse_hypershift_u$i.pcap"
    $err = "$base\synapse_hypershift_u$i.err"
    Start-Process -FilePath 'C:\Program Files\USBPcap\USBPcapCMD.exe' `
        -ArgumentList @('-d', $dev, '-A', '-o', $pcap) `
        -WindowStyle Hidden `
        -RedirectStandardError $err
}
Start-Sleep -Milliseconds 1500
$procs = Get-Process USBPcapCMD -ErrorAction SilentlyContinue
Write-Host "USBPcapCMD running: $($procs.Count)"
Get-ChildItem "$base\synapse_hypershift_u*.err" | ForEach-Object {
    if ($_.Length -gt 0) { Write-Host "--- $($_.Name) ---"; Get-Content $_.FullName }
}
Get-ChildItem "$base\synapse_hypershift_u*.pcap" | ForEach-Object {
    Write-Host "$($_.Name): $($_.Length) bytes"
}
