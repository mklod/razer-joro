# Dump every icon from a list of Windows system DLLs so we can find
# the specific "touch keyboard" glyph Windows shows in the system
# tray. Saves indexed PNGs under assets\sysicons\<dll>\<idx>.png.
# User scans the files in Explorer and tells us which number matches.

Add-Type -AssemblyName System.Drawing

$cs = @"
using System;
using System.Runtime.InteropServices;
public class IconEx {
    [DllImport("shell32.dll", CharSet=CharSet.Auto)]
    public static extern int ExtractIconEx(string lpszFile, int nIconIndex, IntPtr[] phiconLarge, IntPtr[] phiconSmall, int nIcons);
    [DllImport("user32.dll")]
    public static extern bool DestroyIcon(IntPtr hIcon);
}
"@
Add-Type -TypeDefinition $cs -ReferencedAssemblies System.Drawing

$outroot = 'L:\PROJECTS\razer-joro\assets\sysicons'
if (-not (Test-Path $outroot)) { New-Item -ItemType Directory -Path $outroot | Out-Null }

$sources = @(
    'C:\Windows\System32\twinui.dll',
    'C:\Windows\System32\twinui.appcore.dll',
    'C:\Windows\System32\twinui.pcshell.dll',
    'C:\Windows\System32\shellcommon.dll',
    'C:\Windows\System32\ExplorerFrame.dll',
    'C:\Windows\System32\CoreMessaging.dll',
    'C:\Windows\System32\StartTileData.dll',
    'C:\Windows\System32\ActionCenterCPL.dll',
    'C:\Windows\System32\sysmain.dll'
)

foreach ($src in $sources) {
    if (-not (Test-Path $src)) { continue }
    $name = [System.IO.Path]::GetFileNameWithoutExtension($src)
    $outdir = "$outroot\$name"
    if (-not (Test-Path $outdir)) { New-Item -ItemType Directory -Path $outdir | Out-Null }

    $total = [IconEx]::ExtractIconEx($src, -1, $null, $null, 0)
    Write-Output "$name : $total icon groups"

    for ($i = 0; $i -lt $total; $i++) {
        $large = New-Object IntPtr[] 1
        $small = New-Object IntPtr[] 1
        $null = [IconEx]::ExtractIconEx($src, $i, $large, $small, 1)
        if ($large[0] -ne [IntPtr]::Zero) {
            try {
                $ic = [System.Drawing.Icon]::FromHandle($large[0])
                $bmp = $ic.ToBitmap()
                $p = "$outdir\$('{0:0000}' -f $i)_$($bmp.Width).png"
                $bmp.Save($p, [System.Drawing.Imaging.ImageFormat]::Png)
            } catch {}
            [IconEx]::DestroyIcon($large[0]) | Out-Null
        }
        if ($small[0] -ne [IntPtr]::Zero) {
            [IconEx]::DestroyIcon($small[0]) | Out-Null
        }
    }
    Write-Output "  dumped to $outdir"
}
