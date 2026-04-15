#!/usr/bin/env python3
"""Enumerate loaded modules in a Frida target process and filter for BT/GATT related DLLs."""
import sys
import frida

pid = int(sys.argv[1]) if len(sys.argv) > 1 else None
if pid is None:
    print("usage: frida_enum_modules.py <pid>")
    sys.exit(1)

script_src = """
// List all loaded modules, filter for Bluetooth/BLE/GATT keywords
var results = [];
Process.enumerateModules().forEach(function(m) {
    var n = m.name.toLowerCase();
    if (/bluetooth|bth|ble|gatt|wrap|razer|rz|mapping/.test(n)) {
        results.push({
            name: m.name,
            base: m.base.toString(),
            size: m.size,
            path: m.path,
        });
    }
});
send({ type: 'modules', data: results });

// Also list all exports from razer-specific DLLs
var razerMods = Process.enumerateModules().filter(function(m) {
    return /razer|rz|mapping|simple_service|sysutils/i.test(m.name);
});
razerMods.forEach(function(m) {
    try {
        var exp = m.enumerateExports();
        var writeExports = exp.filter(function(e) {
            return /write|send|ble|bt|gatt|character|io/i.test(e.name);
        });
        if (writeExports.length > 0) {
            send({
                type: 'exports',
                module: m.name,
                exports: writeExports.slice(0, 30).map(function(e) { return { name: e.name, addr: e.address.toString() }; }),
            });
        }
    } catch (e) {}
});
"""

device = frida.get_local_device()
session = device.attach(pid)
script = session.create_script(script_src)

def on_message(message, data):
    if message['type'] == 'send':
        import json
        print(json.dumps(message['payload'], indent=2))

script.on('message', on_message)
script.load()

import time
time.sleep(2)
script.unload()
session.detach()
