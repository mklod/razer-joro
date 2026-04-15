$exe = Get-Item "C:\Users\mklod\AppData\Local\razer-joro-target\debug\joro-daemon.exe"
$src = Get-Item "L:\PROJECTS\razer-joro\src\remap.rs"
$fmt = "yyyy-MM-dd HH:mm:ss"
Write-Host ("exe: " + $exe.LastWriteTime.ToString($fmt))
Write-Host ("src: " + $src.LastWriteTime.ToString($fmt))
Write-Host ("now: " + (Get-Date).ToString($fmt))
