#!/usr/bin/env python3
"""Dump exports from BLE-related DLLs in a Frida target, plus a bottom-up
NtDeviceIoControlFile hook."""
import sys, frida, time, json

pid = int(sys.argv[1])

script_src = r"""
var out = {};

function modExports(modName, filterRegex) {
    try {
        var m = Process.findModuleByName(modName);
        if (!m) {
            send({ type: 'missing_module', name: modName });
            return;
        }
        var all = m.enumerateExports();
        var f = all.filter(function(e) { return filterRegex.test(e.name); });
        send({
            type: 'module_exports',
            name: modName,
            totalExports: all.length,
            matched: f.slice(0, 60).map(function(e) { return { n: e.name, a: e.address.toString() }; }),
        });
    } catch (e) {
        send({ type: 'err', where: modName, err: e.toString() });
    }
}

modExports('Windows.Devices.Bluetooth.dll', /./);
modExports('mapping_engine.dll', /write|Write|send|Send|ble|Ble|gatt|Gatt|characteristic|Characteristic|IO|io/);
modExports('bluetoothapis.dll', /./);
modExports('Microsoft.Bluetooth.Proxy.dll', /./);
modExports('BluetoothLEApis.dll', /./);
modExports('windowsdevicesbluetooth.dll', /./);
"""

device = frida.get_local_device()
session = device.attach(pid)
script = session.create_script(script_src)

msgs = []
def on_message(message, data):
    if message['type'] == 'send':
        msgs.append(message['payload'])
    else:
        print("[err]", message, file=sys.stderr)

script.on('message', on_message)
script.load()
time.sleep(2)
script.unload()
session.detach()

for m in msgs:
    print(json.dumps(m, indent=2))
