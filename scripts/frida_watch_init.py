#!/usr/bin/env python3
"""Auto-attach Frida to RazerAppEngine processes as soon as they spawn.
Immediately hooks NtDeviceIoControlFile (filtered to Razer 0x8888 device type)
with both input and delayed-output buffer capture. Also hooks HID.DLL feature
report functions. Captures the first-time init kill command."""
import sys, time, frida, threading

script_src = r"""
function hookNtDIOCF() {
    var addr = Module.findGlobalExportByName('NtDeviceIoControlFile');
    if (!addr) { send({type:'err', msg:'NtDIOCF not found'}); return; }
    function hexify(ptr, len) {
        if (!ptr || len <= 0 || len > 4096) return null;
        try {
            var b = ptr.readByteArray(len);
            if (!b) return '(null)';
            var u8 = new Uint8Array(b);
            var p = [];
            for (var i = 0; i < u8.length; i++) p.push(('00' + u8[i].toString(16)).slice(-2));
            return p.join(' ');
        } catch (e) { return 'ERR:' + e.toString(); }
    }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            this.hFile = args[0];
            this.ioCode = args[5].toInt32() >>> 0;
            this.inBuf = args[6];
            this.inLen = args[7].toInt32();
            this.outBuf = args[8];
            this.outLen = args[9].toInt32();
            var deviceType = (this.ioCode >>> 16) & 0xFFFF;
            this.interesting = (deviceType === 0x8888 || deviceType === 0x41 || deviceType === 0x22 || deviceType === 0x88);
        },
        onLeave: function(retval) {
            if (!this.interesting) return;
            var self = this;
            var inHex = hexify(self.inBuf, self.inLen);
            var outNow = hexify(self.outBuf, self.outLen);
            send({
                type: 'ioctl',
                ioCode: '0x' + self.ioCode.toString(16),
                hFile: self.hFile.toString(),
                inLen: self.inLen,
                outLen: self.outLen,
                inHex: inHex,
                outNow: outNow,
                retval: retval.toString(),
            });
            setTimeout(function() {
                var outLater = hexify(self.outBuf, self.outLen);
                if (outLater !== outNow) {
                    send({ type: 'out_later', ioCode: '0x' + self.ioCode.toString(16), outLater: outLater });
                }
            }, 150);
        }
    });
    send({type:'hooked', name:'NtDeviceIoControlFile', addr:addr.toString()});
}

function hookHidDll() {
    var hid = Process.findModuleByName('HID.DLL');
    if (!hid) { send({type:'miss_hid', msg:'HID.DLL not loaded yet, will retry after mapping_engine loads'}); return false; }
    ['HidD_SetFeature','HidD_GetFeature','HidD_SetOutputReport','HidD_GetInputReport'].forEach(function(name) {
        var addr = null;
        hid.enumerateExports().forEach(function(e) { if (e.name === name) addr = e.address; });
        if (!addr) { send({type:'miss_fn', fn:name}); return; }
        Interceptor.attach(addr, {
            onEnter: function(args) { this.fn = name; this.buf = args[1]; this.len = args[2].toInt32(); },
            onLeave: function(retval) {
                var hex = null;
                if (this.buf && this.len > 0 && this.len < 2048) {
                    try {
                        var b = this.buf.readByteArray(this.len);
                        var u8 = new Uint8Array(b);
                        var p = [];
                        for (var i = 0; i < u8.length; i++) p.push(('00' + u8[i].toString(16)).slice(-2));
                        hex = p.join(' ');
                    } catch (e) {}
                }
                send({type:'hid', fn:this.fn, len:this.len, hex:hex, retval:retval.toString()});
            }
        });
        send({type:'hooked', name:'HID.DLL!'+name});
    });
    return true;
}

hookNtDIOCF();
if (!hookHidDll()) {
    // Retry every 500ms until HID.DLL loads
    var tries = 0;
    var timer = setInterval(function() {
        tries++;
        if (hookHidDll() || tries > 60) {
            clearInterval(timer);
            if (tries > 60) send({type:'err', msg:'HID.DLL never loaded after 30s'});
        }
    }, 500);
}
"""

device = frida.get_local_device()
start = time.time()
attached = set()
sessions = []
lock = threading.Lock()

def on_message(pid, message, data):
    with lock:
        ts = time.time() - start
        if message['type'] == 'send':
            p = message['payload']
            t = p.get('type')
            if t == 'ioctl':
                print(f"[{ts:8.3f}] [{pid}] IOCTL {p['ioCode']}  hFile={p['hFile']}  inLen={p['inLen']}  outLen={p['outLen']}  ret={p['retval']}", flush=True)
                if p.get('inHex'): print(f"             in:  {p['inHex']}", flush=True)
                if p.get('outNow'): print(f"             out: {p['outNow']}", flush=True)
            elif t == 'out_later':
                print(f"[{ts:8.3f}] [{pid}] LATER {p['ioCode']}: {p.get('outLater')}", flush=True)
            elif t == 'hid':
                print(f"[{ts:8.3f}] [{pid}] {p['fn']}  len={p['len']}  ret={p['retval']}", flush=True)
                if p.get('hex'): print(f"             hex: {p['hex']}", flush=True)
            elif t == 'hooked':
                print(f"[{ts:8.3f}] [{pid}] HOOKED {p['name']}", flush=True)
            elif t in ('miss_hid', 'miss_fn'):
                print(f"[{ts:8.3f}] [{pid}] {p}", flush=True)
            elif t == 'err':
                print(f"[{ts:8.3f}] [{pid}] ERR {p}", flush=True)

def mk_handler(pid):
    return lambda m, d: on_message(pid, m, d)

print("Waiting for RazerAppEngine processes... (Ctrl+C to stop)", flush=True)
try:
    while True:
        procs = device.enumerate_processes()
        for p in procs:
            if p.name == 'RazerAppEngine.exe' and p.pid not in attached:
                attached.add(p.pid)
                try:
                    sess = device.attach(p.pid)
                    script = sess.create_script(script_src)
                    script.on('message', mk_handler(p.pid))
                    script.load()
                    sessions.append((p.pid, sess, script))
                    print(f"[{time.time()-start:8.3f}] [+] Attached {p.pid}", flush=True)
                except Exception as e:
                    if 'frida-agent' not in str(e):
                        print(f"[{time.time()-start:8.3f}] [-] {p.pid}: {e}", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    for pid, s, scr in sessions:
        try: scr.unload(); s.detach()
        except: pass
