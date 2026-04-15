// Frida hook to capture BLE GATT writes from Razer processes
// Hooks both Win32 BluetoothAPIs.dll and windows.devices.bluetooth.dll
// where WinRT GattCharacteristic.WriteValueAsync bottoms out.

function hexDump(ptr, len) {
    var bytes = Memory.readByteArray(ptr, len);
    return hexdump(bytes, { offset: 0, length: len, header: false, ansi: false });
}

// --- Win32 path: BluetoothGATTSetCharacteristicValue ---
// HRESULT BluetoothGATTSetCharacteristicValue(
//     HANDLE hDevice,
//     PBTH_LE_GATT_CHARACTERISTIC Characteristic,
//     PBTH_LE_GATT_CHARACTERISTIC_VALUE CharacteristicValue,  // {ULONG DataSize; UCHAR Data[anysize];}
//     BTH_LE_GATT_RELIABLE_WRITE_CONTEXT ReliableWriteContext,
//     ULONG Flags);
try {
    var fn = Module.getExportByName('bluetoothapis.dll', 'BluetoothGATTSetCharacteristicValue');
    Interceptor.attach(fn, {
        onEnter: function(args) {
            try {
                var pVal = args[2];
                if (!pVal.isNull()) {
                    var dataSize = pVal.readU32();
                    if (dataSize > 0 && dataSize < 4096) {
                        var dataPtr = pVal.add(4);
                        console.log('\n[Win32 BluetoothGATTSetCharacteristicValue] size=' + dataSize);
                        console.log(hexDump(dataPtr, dataSize));
                    }
                }
            } catch (e) { console.log('[hook err Win32] ' + e); }
        }
    });
    console.log('[+] Hooked bluetoothapis.dll!BluetoothGATTSetCharacteristicValue');
} catch (e) {
    console.log('[-] Could not hook Win32 GATT write: ' + e);
}

// --- WinRT path ---
// GattCharacteristic::WriteValueAsync and WriteValueWithResultAsync are
// implemented in windows.devices.bluetooth.dll. Without symbols we can hook
// by export prefix pattern. Many WinRT methods are accessible as "WinRTFactoryStub"
// vtable entries. The cleanest user-mode hook point is Bluetooth!GATT_SendWriteRequest
// or BthLEClient!GATT_WriteCharacteristic (undocumented).
//
// Fallback: intercept WriteFile and DeviceIoControl from BluetoothLE kernel IPC
// bottom layer in the same module if we can find the right export.

// Enumerate exports of windows.devices.bluetooth.dll that look like GATT writes
try {
    var mod = Process.findModuleByName('windows.devices.bluetooth.dll');
    if (mod) {
        var exports = mod.enumerateExports();
        var gattWrites = exports.filter(function(e) {
            return /gatt.*write|write.*gatt|WriteValue/i.test(e.name);
        });
        console.log('[info] windows.devices.bluetooth.dll GATT write exports: ' + gattWrites.length);
        gattWrites.slice(0, 10).forEach(function(e) {
            console.log('  ' + e.name + ' @ ' + e.address);
        });
    } else {
        console.log('[-] windows.devices.bluetooth.dll not loaded in target');
    }
} catch (e) { console.log('[-] windows.devices.bluetooth enum err: ' + e); }

// --- Generic fallback: hook NtWriteFile for handles that look like BTH device ---
// This is too noisy to enable by default. Uncomment if specific hooks miss the event.

console.log('[ready] Frida hooks installed. Waiting for BLE writes...');
