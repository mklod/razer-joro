#!/usr/bin/env python3
"""Attach Frida to a specific PID and hook EVERY export of mapping_engine.dll.
Also track when the DLL loads via ModuleInitializer."""
import sys, frida, time, threading

pid = int(sys.argv[1])

script_src = r"""
function installHooks() {
    var mod = Process.findModuleByName('mapping_engine.dll');
    if (!mod) return false;
    var exports = mod.enumerateExports();
    var hookedCount = 0;
    var failed = 0;
    exports.forEach(function(e) {
        try {
            Interceptor.attach(e.address, {
                onEnter: function(args) {
                    var a = [];
                    for (var i = 0; i < 4; i++) a.push(args[i].toString());
                    send({ type: 'call', func: e.name, args: a });
                }
            });
            hookedCount++;
        } catch (err) { failed++; }
    });
    send({ type: 'ready', hooked: hookedCount, failed: failed, total: exports.length });
    return true;
}

if (!installHooks()) {
    send({ type: 'waiting', msg: 'mapping_engine.dll not loaded yet, watching for it' });
    // Watch for module load via LoadLibraryExW hook
    var LoadLibraryExW = Module.findGlobalExportByName('LoadLibraryExW');
    if (LoadLibraryExW) {
        Interceptor.attach(LoadLibraryExW, {
            onEnter: function(args) {
                try {
                    var name = Memory.readUtf16String(args[0]);
                    if (name && /mapping_engine\.dll/i.test(name)) {
                        this.isTarget = true;
                    }
                } catch (e) {}
            },
            onLeave: function(retval) {
                if (this.isTarget) {
                    send({ type: 'loaded', msg: 'mapping_engine.dll just loaded, installing hooks' });
                    setTimeout(installHooks, 100);
                }
            }
        });
    }
}
"""

device = frida.get_local_device()
session = device.attach(pid)
script = session.create_script(script_src)
start = time.time()
lock = threading.Lock()

def on_message(message, data):
    with lock:
        ts = time.time() - start
        if message['type'] == 'send':
            p = message['payload']
            t = p.get('type')
            if t == 'call':
                print(f"[{ts:8.3f}] {p['func']}  ({' '.join(p['args'])})", flush=True)
            else:
                print(f"[{ts:8.3f}] {t.upper()} {p}", flush=True)
        elif message['type'] == 'error':
            print(f"[{ts:8.3f}] ERR {message.get('description')}", flush=True)

script.on('message', on_message)
script.load()
print(f"[+] Attached {pid}. Ctrl+C to stop.", flush=True)

try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    try:
        script.unload()
        session.detach()
    except: pass
