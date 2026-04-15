#!/usr/bin/env python3
"""Hook hidapi write functions in the Razer .node module. Captures full buffer
contents when Synapse does any HID write (output or feature report)."""
import sys, frida, time, threading

pid = int(sys.argv[1])

script_src = r"""
var mod = Process.findModuleByName('09f023f5-07a5-4211-a02d-804c309bbbb2.tmp.node');
if (!mod) {
    send({ type: 'err', msg: 'hidapi module not found' });
} else {
    var targets = ['hid_write', 'hid_send_feature_report', 'hid_get_feature_report', 'hid_open_path', 'hid_read', 'hid_read_timeout'];
    targets.forEach(function(name) {
        var addr = null;
        mod.enumerateExports().forEach(function(e) {
            if (e.name === name) addr = e.address;
        });
        if (!addr) { send({ type: 'miss', name: name }); return; }
        Interceptor.attach(addr, {
            onEnter: function(args) {
                this.fn = name;
                this.dev = args[0];
                this.data = args[1];
                this.len = args[2].toInt32();
            },
            onLeave: function(retval) {
                try {
                    var bytes = null;
                    if (this.fn === 'hid_open_path') {
                        // args[0] is the path, null-terminated char string
                        var p = null;
                        try { p = Memory.readCString(this.dev, 512); } catch (e) {}
                        send({ type: 'call', fn: this.fn, path: p, retval: retval.toString() });
                        return;
                    }
                    if (this.len > 0 && this.len < 512) {
                        bytes = Memory.readByteArray(this.data, this.len);
                    }
                    var hex = null;
                    if (bytes) {
                        var u8 = new Uint8Array(bytes);
                        var parts = [];
                        for (var i = 0; i < u8.length; i++) {
                            parts.push(('00' + u8[i].toString(16)).slice(-2));
                        }
                        hex = parts.join(' ');
                    }
                    send({
                        type: 'call',
                        fn: this.fn,
                        dev: this.dev.toString(),
                        len: this.len,
                        hex: hex,
                        retval: retval.toString(),
                    });
                } catch (e) {
                    send({ type: 'err', where: this.fn, err: e.toString() });
                }
            }
        });
        send({ type: 'hooked', name: name, addr: addr.toString() });
    });
}
"""

device = frida.get_local_device()
sess = device.attach(pid)
script = sess.create_script(script_src)
start = time.time()
lock = threading.Lock()

def on_message(message, data):
    with lock:
        ts = time.time() - start
        if message['type'] == 'send':
            p = message['payload']
            t = p.get('type')
            if t == 'call':
                if p['fn'] == 'hid_open_path':
                    print(f"[{ts:8.3f}] hid_open_path  path={p.get('path')}  ret={p.get('retval')}", flush=True)
                elif p['fn'] in ('hid_read', 'hid_read_timeout'):
                    # Too noisy — suppress unless len > 0 and non-zero data
                    if p['hex'] and any(x != '00' for x in p['hex'].split()):
                        print(f"[{ts:8.3f}] {p['fn']}  len={p['len']}  {p['hex']}", flush=True)
                else:
                    print(f"[{ts:8.3f}] {p['fn']}  dev={p['dev']}  len={p['len']}", flush=True)
                    if p['hex']:
                        print(f"             hex: {p['hex']}", flush=True)
            elif t == 'hooked':
                print(f"[{ts:8.3f}] HOOKED {p['name']} @ {p['addr']}", flush=True)
            elif t == 'miss':
                print(f"[{ts:8.3f}] MISS {p['name']}", flush=True)
            elif t == 'err':
                print(f"[{ts:8.3f}] ERR {p}", flush=True)
        elif message['type'] == 'error':
            print(f"[{ts:8.3f}] SCRIPT ERR {message.get('description')}", flush=True)

script.on('message', on_message)
script.load()
print(f"[+] Hooked PID {pid}. Ctrl+C to stop.", flush=True)

try:
    while True: time.sleep(1)
except KeyboardInterrupt:
    try: script.unload(); sess.detach()
    except: pass
