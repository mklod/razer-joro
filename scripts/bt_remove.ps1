$code = @"
using System;
using System.Runtime.InteropServices;
public class Bt {
    [StructLayout(LayoutKind.Sequential)]
    public struct BLUETOOTH_ADDRESS { public ulong Value; }
    [DllImport("irprops.cpl", EntryPoint="BluetoothRemoveDevice")]
    public static extern uint BluetoothRemoveDevice(ref BLUETOOTH_ADDRESS addr);
}
"@
Add-Type -TypeDefinition $code
$a = New-Object Bt+BLUETOOTH_ADDRESS
$a.Value = [Convert]::ToUInt64("C8E2775D2FA2", 16)
$r = [Bt]::BluetoothRemoveDevice([ref]$a)
$hex = "{0:X8}" -f $r
Write-Host "BluetoothRemoveDevice returned: 0x$hex"
