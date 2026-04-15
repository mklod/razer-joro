#!/usr/bin/env python3
"""Hook every export of mapping_engine.dll in every RazerAppEngine process.
Lightweight — logs function name on entry only. Watches for newly spawned
Razer processes and auto-attaches."""
import sys, time, frida, threading

TARGETS = ('RazerAppEngine.exe',)

script_src = r"""
var mod = Process.findModuleByName('mapping_engine.dll');
if (!mod) {
    send({ type: 'skip', msg: 'mapping_engine.dll not loaded' });
} else {
    var exports = mod.enumerateExports();
    var hookedCount = 0;
    var failed = 0;
    exports.forEach(function(e) {
        // Skip underscore/leading-? decorated dupes; keep both plain and decorated forms
        try {
            Interceptor.attach(e.address, {
                onEnter: function(args) {
                    // Try to capture first 4 args as hex; also attempt C string read
                    var a = [];
                    for (var i = 0; i < 4; i++) {
                        var v = args[i];
                        a.push(v.toString());
                    }
                    send({ type: 'call', func: e.name, args: a });
                }
            });
            hookedCount++;
        } catch (err) {
            failed++;
        }
    });
    send({ type: 'ready', hooked: hookedCount, failed: failed, total: exports.length });
}
"""

lock = threading.Lock()
start = time.time()
attached_pids = set()

def mk_handler(pid):
    def handler(message, data):
        with lock:
            ts = time.time() - start
            if message['type'] == 'send':
                p = message['payload']
                t = p.get('type')
                if t == 'call':
                    argstr = ' '.join(p['args'])
                    print(f"[{ts:8.3f}] [{pid}] {p['func']}  args=({argstr})", flush=True)
                elif t == 'ready':
                    print(f"[{ts:8.3f}] [{pid}] READY hooked={p['hooked']} failed={p['failed']} total={p['total']}", flush=True)
                elif t == 'skip':
                    print(f"[{ts:8.3f}] [{pid}] SKIP {p['msg']}", flush=True)
            elif message['type'] == 'error':
                print(f"[{ts:8.3f}] [{pid}] SCRIPT ERR {message.get('description')}", flush=True)
    return handler

device = frida.get_local_device()
print("Waiting for RazerAppEngine.exe processes... (Ctrl+C to stop)", flush=True)

sessions = []
try:
    while True:
        procs = device.enumerate_processes()
        for p in procs:
            if p.name in TARGETS and p.pid not in attached_pids:
                try:
                    sess = device.attach(p.pid)
                    script = sess.create_script(script_src)
                    script.on('message', mk_handler(p.pid))
                    script.load()
                    sessions.append((p.pid, sess, script))
                    attached_pids.add(p.pid)
                    print(f"[{time.time()-start:8.3f}] [+] Attached {p.pid} {p.name}", flush=True)
                except Exception as e:
                    attached_pids.add(p.pid)  # don't retry
                    msg = str(e)
                    if 'frida-agent' not in msg:
                        print(f"[{time.time()-start:8.3f}] [-] {p.pid} {p.name}: {msg}", flush=True)
        time.sleep(0.5)
except KeyboardInterrupt:
    for pid, sess, script in sessions:
        try:
            script.unload()
            sess.detach()
        except: pass
