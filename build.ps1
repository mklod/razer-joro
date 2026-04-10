# build.ps1 — Wrapper to build joro-daemon from SMB source drive
# Last modified: 2026-04-09--2200
#
# Required because:
#   1. Source is on SMB share (L:\) — build scripts can't execute from there,
#      so target dir is redirected to local AppData.
#   2. Git's link.exe shadows MSVC's — we set the linker in .cargo/config.toml,
#      but LIB/INCLUDE env vars must also be set for MSVC to find Windows SDK.
#
# Usage: .\build.ps1 [cargo args...]  e.g. .\build.ps1 build --release

$MSVC_VER = "14.44.35207"
$MSVC_BASE = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Tools\MSVC\$MSVC_VER"
$WINSDK_VER = "10.0.26100.0"
$WINSDK_BASE = "C:\Program Files (x86)\Windows Kits\10"

$env:PATH = "C:\Users\$env:USERNAME\.cargo\bin;$MSVC_BASE\bin\Hostx64\x64;" + $env:PATH
$env:LIB = "$MSVC_BASE\lib\x64;$WINSDK_BASE\Lib\$WINSDK_VER\um\x64;$WINSDK_BASE\Lib\$WINSDK_VER\ucrt\x64"
$env:INCLUDE = "$MSVC_BASE\include;$WINSDK_BASE\Include\$WINSDK_VER\ucrt;$WINSDK_BASE\Include\$WINSDK_VER\um;$WINSDK_BASE\Include\$WINSDK_VER\shared"

Set-Location $PSScriptRoot

$cargoArgs = if ($args.Count -eq 0) { @("build") } else { $args }
& "C:\Users\$env:USERNAME\.cargo\bin\cargo.exe" @cargoArgs
