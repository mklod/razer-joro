"""
Find Synapse's Fn-state detection channel through dongle.

Hooks the candidate APIs:
  - RegisterRawInputDevices (user32) — subscription to raw HID
  - GetRawInputData (user32) — pulling raw HID input
  - HidD_GetInputReport (hid.dll) — solicited input poll
  - NtReadFile (ntdll) — direct ReadFile on HID handles
  - NtDeviceIoControlFile — ALL ioctls (filter by hFile path = dongle/rzcontrol)

Also keeps the file-handle map (NtCreateFile) so we can resolve hFile -> path.

Run AFTER Synapse is launched and Joro is connected via dongle. Then
hold Fn for 3s, release. Stop with Ctrl+C.
"""
import sys, time, frida, threading

SCRIPT = r"""
var fileMap = {};
function readUStr(usPtr) {
    if (!usPtr || usPtr.isNull()) return null;
    try {
        var len = usPtr.readU16();
        var buf = usPtr.add(8).readPointer();
        if (!buf || buf.isNull() || len === 0) return null;
        return buf.readUtf16String(len / 2);
    } catch (e) { return null; }
}
function hookCreate(name) {
    var addr = Module.getGlobalExportByName(name);
    if (!addr) return;
    Interceptor.attach(addr, {
        onEnter: function(args) {
            this.outHandle = args[0];
            this.objAttrs = args[2];
            try {
                if (this.objAttrs && !this.objAttrs.isNull()) {
                    var nmPtr = this.objAttrs.add(16).readPointer();
                    this.path = readUStr(nmPtr);
                }
            } catch (e) { this.path = null; }
        },
        onLeave: function() {
            try {
                if (this.outHandle && !this.outHandle.isNull()) {
                    var h = this.outHandle.readPointer();
                    if (h && this.path) {
                        fileMap[h.toString()] = this.path;
                    }
                }
            } catch (e) {}
        }
    });
}
function hookClose() {
    var addr = Module.getGlobalExportByName('NtClose');
    if (!addr) return;
    Interceptor.attach(addr, {
        onEnter: function(args) {
            try { delete fileMap[args[0].toString()]; } catch (e) {}
        }
    });
}
function isInteresting(path) {
    if (!path) return false;
    var p = path.toLowerCase();
    return p.indexOf('vid_1532') >= 0 ||
           p.indexOf('009c') >= 0 ||
           p.indexOf('rzcontrol') >= 0;
}
function hexify(ptr, len) {
    if (!ptr || len <= 0 || len > 256) return null;
    try {
        var u8 = new Uint8Array(ptr.readByteArray(len));
        var p = [];
        for (var i = 0; i < u8.length; i++) p.push(('00' + u8[i].toString(16)).slice(-2));
        return p.join(' ');
    } catch (e) { return 'ERR'; }
}

// 1. RegisterRawInputDevices
var u32mod = Process.findModuleByName('user32.dll');
var rriPtr = u32mod ? u32mod.findExportByName('RegisterRawInputDevices') : null;
if (rriPtr) {
    Interceptor.attach(rriPtr, {
        onEnter: function(args) {
            var ridPtr = args[0];
            var count = args[1].toInt32();
            var size = args[2].toInt32();
            try {
                for (var i = 0; i < count; i++) {
                    var ent = ridPtr.add(i * size);
                    var up = ent.readU16();
                    var u = ent.add(2).readU16();
                    var fl = ent.add(4).readU32();
                    send({type:'rri', i:i, count:count, up:up, u:u, fl:'0x'+fl.toString(16)});
                }
            } catch (e) {
                send({type:'log', msg:'rri parse err: ' + e});
            }
        }
    });
    send({type:'log', msg:'hooked RegisterRawInputDevices'});
}

// 2. GetRawInputData (logs only when called — we don't try to parse the reply)
var gridPtr = u32mod ? u32mod.findExportByName('GetRawInputData') : null;
if (gridPtr) {
    var n_grid = 0;
    Interceptor.attach(gridPtr, {
        onLeave: function() {
            n_grid++;
            if (n_grid <= 60 || (n_grid % 100) === 0) {
                send({type:'grid', n:n_grid});
            }
        }
    });
    send({type:'log', msg:'hooked GetRawInputData'});
}

// 3. HidD_GetInputReport
var mod = Process.findModuleByName('hid.dll');
if (mod) {
    var hgip = mod.findExportByName('HidD_GetInputReport');
    if (hgip) {
        Interceptor.attach(hgip, {
            onEnter: function(a) {
                this.h = a[0];
                this.buf = a[1];
                this.len = a[2] ? a[2].toInt32() : 0;
            },
            onLeave: function(rv) {
                var path = fileMap[this.h.toString()] || '';
                if (isInteresting(path)) {
                    send({type:'hgip', ret: rv.toInt32(), len: this.len,
                          path: path,
                          hex: hexify(this.buf, Math.min(this.len, 64))});
                }
            }
        });
        send({type:'log', msg:'hooked HidD_GetInputReport'});
    }
}

// 4. NtReadFile
var nrf = Module.getGlobalExportByName('NtReadFile');
if (nrf) {
    Interceptor.attach(nrf, {
        onEnter: function(a) {
            this.h = a[0];
            this.buf = a[5];
            this.len = a[6] ? a[6].toInt32() : 0;
        },
        onLeave: function(rv) {
            var path = fileMap[this.h.toString()] || '';
            if (isInteresting(path)) {
                send({type:'ntread', ret: '0x'+(rv.toInt32() >>> 0).toString(16),
                      len: this.len, path: path,
                      hex: hexify(this.buf, Math.min(this.len, 64))});
            }
        }
    });
    send({type:'log', msg:'hooked NtReadFile'});
}

// 5. NtDeviceIoControlFile — log ALL ioctls on dongle path (not just Razer-class)
var ndioc = Module.getGlobalExportByName('NtDeviceIoControlFile');
if (ndioc) {
    Interceptor.attach(ndioc, {
        onEnter: function(args) {
            this.h = args[0];
            this.code = args[5].toInt32() >>> 0;
            this.inBuf = args[6];
            this.inLen = args[7].toInt32();
            this.outBuf = args[8];
            this.outLen = args[9].toInt32();
        },
        onLeave: function(rv) {
            var path = fileMap[this.h.toString()] || '';
            if (!isInteresting(path)) return;
            send({type:'ioctl', code:'0x'+this.code.toString(16),
                  ret:'0x'+(rv.toInt32() >>> 0).toString(16),
                  inLen: this.inLen, outLen: this.outLen, path: path,
                  inHex: this.inLen > 0 ? hexify(this.inBuf, Math.min(this.inLen, 32)) : '',
                  outHex: this.outLen > 0 ? hexify(this.outBuf, Math.min(this.outLen, 32)) : ''});
        }
    });
    send({type:'log', msg:'hooked NtDeviceIoControlFile'});
}

hookCreate('NtCreateFile');
hookCreate('NtOpenFile');
hookClose();
send({type:'log', msg:'all hooks installed'});
"""

def main():
    device = frida.get_local_device()
    start = time.time()
    attached = set()
    sessions = []
    lock = threading.Lock()

    def on_message(pid, message, data):
        with lock:
            ts = time.time() - start
            if message['type'] != 'send':
                if message['type'] == 'error':
                    print(f"[{ts:7.2f}] [{pid}] ERR: {message}", flush=True)
                return
            p = message['payload']
            t = p.get('type')
            if t == 'log':
                print(f"[{ts:7.2f}] [{pid}] {p['msg']}", flush=True)
            elif t == 'rri':
                print(f"[{ts:7.2f}] [{pid}] *** RegisterRawInputDevices ***  i={p['i']}/{p['count']}  usage_page=0x{p['up']:04x}  usage=0x{p['u']:04x}  flags={p['fl']}", flush=True)
            elif t == 'grid':
                print(f"[{ts:7.2f}] [{pid}] GetRawInputData call #{p['n']}", flush=True)
            elif t == 'hgip':
                print(f"[{ts:7.2f}] [{pid}] HidD_GetInputReport ret={p['ret']} len={p['len']}  path-tail={p['path'][-50:]}", flush=True)
                if p.get('hex'): print(f"             {p['hex']}", flush=True)
            elif t == 'ntread':
                print(f"[{ts:7.2f}] [{pid}] NtReadFile ret={p['ret']} len={p['len']}  path-tail={p['path'][-50:]}", flush=True)
                if p.get('hex'): print(f"             {p['hex']}", flush=True)
            elif t == 'ioctl':
                print(f"[{ts:7.2f}] [{pid}] ioctl={p['code']} ret={p['ret']} inLen={p['inLen']} outLen={p['outLen']}  path-tail={p['path'][-50:]}", flush=True)
                if p.get('inHex'): print(f"             in:  {p['inHex']}", flush=True)
                if p.get('outHex'): print(f"             out: {p['outHex']}", flush=True)

    def mk_handler(pid):
        return lambda m, d: on_message(pid, m, d)

    TARGET_NAMES = ('RazerAppEngine.exe', 'razer_elevation_service.exe',
                    'Razer Synapse Service.exe', 'Razer Central Service.exe',
                    'RzEngineMon.exe', 'GameManagerService3.exe',
                    'rzactionsvc.exe', 'RzActionService.exe')
    print(f"Hooking Razer processes... press Fn for 3s while running. Ctrl+C to stop.", flush=True)
    try:
        while True:
            for p in device.enumerate_processes():
                if p.name in TARGET_NAMES and p.pid not in attached:
                    attached.add(p.pid)
                    try:
                        sess = device.attach(p.pid)
                        scr = sess.create_script(SCRIPT)
                        scr.on('message', mk_handler(p.pid))
                        scr.load()
                        sessions.append((p.pid, sess, scr))
                        print(f"[{time.time()-start:7.2f}] [+] Attached {p.pid} ({p.name})", flush=True)
                    except Exception as e:
                        if 'frida-agent' not in str(e):
                            pass
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
        for pid, s, scr in sessions:
            try: scr.unload(); s.detach()
            except: pass

if __name__ == "__main__":
    main()
