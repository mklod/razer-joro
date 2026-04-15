#!/usr/bin/env python3
"""Hook mapping_engine.dll enable/disable/mapping exports in all RazerAppEngine
processes. Logs every call with arguments, backtrace, and timing."""
import sys, time, frida, threading

TARGET_DLL = 'mapping_engine.dll'
TARGETS_FILTER = ('RazerAppEngine.exe',)

HOOK_NAMES = [
    'enableAllDevicesHooks',
    'disableAllDevicesHooks',
    'enableMapping',
    'disableMapping',
    'enableInputRedirect',
    'disableInputRedirect',
    'enableKeyboardInputRedirect',
    'disableKeyboardInputRedirect',
    'enableRazerKeyInputRedirect',
    'disableRazerKeyInputRedirect',
    'enableMouseInputRedirect',
    'disableMouseInputRedirect',
    'enableMouseMoveRedirect',
    'disableMouseMoveRedirect',
    'enableGlobalShortcut',
    'disableGlobalShortcut',
    'isMappingEnabled',
    'isAllDevicesHooksEnabled',
    'setInputNotificationCallback',
    'registerInputNotification',
    'unregisterInputNotification',
]

script_src = """
var target = %HOOK_NAMES%;
var mod = Process.findModuleByName('mapping_engine.dll');
if (!mod) {
    send({ type: 'err', msg: 'mapping_engine.dll not loaded' });
} else {
    var exports = mod.enumerateExports();
    var hookedCount = 0;
    target.forEach(function(name) {
        // Find both the plain name and the decorated C++ variant
        var match = exports.find(function(e) { return e.name === name; });
        if (!match) {
            match = exports.find(function(e) {
                return e.name.indexOf('@' + name + '@') > 0 || e.name.indexOf('?' + name + '@') >= 0;
            });
        }
        if (!match) {
            send({ type: 'miss', name: name });
            return;
        }
        try {
            Interceptor.attach(match.address, {
                onEnter: function(args) {
                    try {
                        // Grab first 8 arg values as hex + attempt to read as C strings if pointers look valid
                        var a = [];
                        for (var i = 0; i < 6; i++) {
                            var v = args[i];
                            var o = { hex: v.toString() };
                            try {
                                // Try to read a wide string (WinRT) then narrow string
                                var str = Memory.readCString(v, 128);
                                if (str && str.length > 0 && /^[\\x20-\\x7e]+$/.test(str)) {
                                    o.str = str;
                                }
                            } catch (e) {}
                            a.push(o);
                        }
                        send({
                            type: 'call',
                            func: name,
                            args: a,
                            ts: new Date().getTime(),
                        });
                    } catch (e) {
                        send({ type: 'err', where: name, err: e.toString() });
                    }
                }
            });
            hookedCount++;
        } catch (e) {
            send({ type: 'err', where: name, err: e.toString() });
        }
    });
    send({ type: 'ready', hooked: hookedCount, totalRequested: target.length });
}
""".replace('%HOOK_NAMES%', str(HOOK_NAMES))

device = frida.get_local_device()
procs = [p for p in device.enumerate_processes() if p.name in TARGETS_FILTER]
print(f"Found {len(procs)} target processes", flush=True)

sessions = []
lock = threading.Lock()
start = time.time()

def mk_handler(pid, name):
    def handler(message, data):
        with lock:
            ts = time.time() - start
            if message['type'] == 'send':
                p = message['payload']
                t = p.get('type')
                if t == 'call':
                    args_repr = ' '.join(
                        f"arg{i}={a['hex']}" + (f"='{a['str']}'" if 'str' in a else '')
                        for i, a in enumerate(p['args'])
                    )
                    print(f"[{ts:7.3f}] [{pid}] {p['func']}: {args_repr}", flush=True)
                elif t == 'ready':
                    print(f"[{ts:7.3f}] [{pid}] READY hooked={p['hooked']}/{p['totalRequested']}", flush=True)
                elif t == 'miss':
                    print(f"[{ts:7.3f}] [{pid}] MISS {p['name']}", flush=True)
                elif t == 'err':
                    print(f"[{ts:7.3f}] [{pid}] ERR {p.get('where', '?')}: {p.get('err') or p.get('msg')}", flush=True)
            elif message['type'] == 'error':
                print(f"[{ts:7.3f}] [{pid}] SCRIPT ERR {message.get('description')}", flush=True)
    return handler

for p in procs:
    try:
        sess = device.attach(p.pid)
        script = sess.create_script(script_src)
        script.on('message', mk_handler(p.pid, p.name))
        script.load()
        sessions.append((p.pid, sess, script))
        print(f"[+] {p.pid} {p.name}", flush=True)
    except Exception as e:
        print(f"[-] {p.pid} {p.name}: {e}", flush=True)

print(f"\n[*] {len(sessions)} sessions active. Ctrl+C to stop.", flush=True)

try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    for pid, sess, script in sessions:
        try:
            script.unload()
            sess.detach()
        except: pass
