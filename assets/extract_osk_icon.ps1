# Extract the Windows tray-keyboard icon in every embedded resolution
# and save each as a PNG. We use Microsoft's touch-keyboard glyph
# because our hand-rendered keyboard kept looking mushy at 16px no
# matter how we tweaked the PIL recipe.
#
# Source is `TextInputHost.exe` (the Windows 10/11 modern touch
# keyboard process) — matches the icon the user sees in their taskbar
# today. `osk.exe` has a DIFFERENT icon and was the wrong choice;
# keeping this as a fallback anyway.
#
# License note: these are Windows system binaries; their icons are
# Microsoft copyright. This project uses them privately (personal
# Synapse replacement). Don't ship binaries embedding Microsoft
# icons to third parties without understanding the implications.

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

$outdir = 'L:\PROJECTS\razer-joro\assets'

# Primary source: the Windows 10/11 modern touch-keyboard host. Its
# icon is what the user actually sees in the system tray today.
$candidates = @(
    'C:\Windows\SystemApps\MicrosoftWindows.Client.CBS_cw5n1h2txyewy\TextInputHost.exe',
    'C:\Program Files\Common Files\microsoft shared\ink\TabTip.exe',
    'C:\Windows\System32\osk.exe'
)

$src = $null
foreach ($c in $candidates) {
    if (Test-Path $c) { $src = $c; break }
}
if (-not $src) { Write-Error 'No keyboard icon source found'; exit 1 }
Write-Output "Using icon source: $src"

# Enumerate every icon index in the file and dump the largest/smallest
# bitmap for each. Index 0 is usually the primary app icon; additional
# indices can contain alternate sizes.
$total = [IconEx]::ExtractIconEx($src, -1, $null, $null, 0)
Write-Output "Total icon groups in file: $total"

$bestLarge = $null
$bestLargeSize = 0
for ($i = 0; $i -lt $total; $i++) {
    $large = New-Object IntPtr[] 1
    $small = New-Object IntPtr[] 1
    $n = [IconEx]::ExtractIconEx($src, $i, $large, $small, 1)
    if ($large[0] -ne [IntPtr]::Zero) {
        $ic = [System.Drawing.Icon]::FromHandle($large[0])
        $bmp = $ic.ToBitmap()
        $p = "$outdir\osk_src_${i}_large_$($bmp.Width).png"
        $bmp.Save($p, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-Output "  [$i] large $($bmp.Width)x$($bmp.Height) -> $($p | Split-Path -Leaf)"
        if ($bmp.Width -gt $bestLargeSize) {
            $bestLargeSize = $bmp.Width
            $bestLarge = $bmp
        }
        [IconEx]::DestroyIcon($large[0]) | Out-Null
    }
    if ($small[0] -ne [IntPtr]::Zero) {
        $ic2 = [System.Drawing.Icon]::FromHandle($small[0])
        $bmp2 = $ic2.ToBitmap()
        $p = "$outdir\osk_src_${i}_small_$($bmp2.Width).png"
        $bmp2.Save($p, [System.Drawing.Imaging.ImageFormat]::Png)
        Write-Output "  [$i] small $($bmp2.Width)x$($bmp2.Height) -> $($p | Split-Path -Leaf)"
        [IconEx]::DestroyIcon($small[0]) | Out-Null
    }
}

# Emit `osk_large.png` as the authoritative tray source for gen_icon.py
# to post-process. Use the highest-res icon we found.
if ($bestLarge -ne $null) {
    $bestLarge.Save("$outdir\osk_large.png", [System.Drawing.Imaging.ImageFormat]::Png)
    Write-Output "Wrote osk_large.png ($($bestLarge.Width)x$($bestLarge.Height))"
}
