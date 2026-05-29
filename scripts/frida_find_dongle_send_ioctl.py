#!/usr/bin/env python3
"""
Find the IOCTL Synapse uses to push Razer Protocol30 lighting commands to
the Joro dongle.

Strategy:
1. Hook NtCreateFile / NtOpenFile / NtClose on every Razer process, build
   a handle->devicepath map.
2. Hook NtDeviceIoControlFile and HidD_SetFeature.
3. Filter for input buffers containing a Protocol30 packet (offset 6 = 0x0F
   lighting class). Print IOCTL code, device path of hFile, full hex.

Run:
    1) start Synapse, wait for it to settle
    2) python frida_find_dongle_send_ioctl.py
    3) in Synapse: Joro -> Lighting -> change color
    4) Ctrl+C; the printed log shows the IOCTL + device path
"""
import sys, time, frida, threading

SCRIPT = r"""
// Map hFile -> device path. NtCreateFile gets the OBJECT_ATTRIBUTES.ObjectName
// (UNICODE_STRING) at args[2]; we read its Buffer.
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
                    // OBJECT_ATTRIBUTES { ULONG Length; HANDLE Root; PUNICODE_STRING ObjectName; ... }
                    // 64-bit: Length=4, padding=4, RootDir=8, ObjectName=8 -> at offset 16
                    var nmPtr = this.objAttrs.add(16).readPointer();
                    this.path = readUStr(nmPtr);
                }
            } catch (e) { this.path = null; }
        },
        onLeave: function(retval) {
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

function hexify(ptr, len) {
    if (!ptr || len <= 0 || len > 4096) return null;
    try {
        var u8 = new Uint8Array(ptr.readByteArray(len));
        var p = [];
        for (var i = 0; i < u8.length; i++) p.push(('00' + u8[i].toString(16)).slice(-2));
        return p.join(' ');
    } catch (e) { return 'ERR'; }
}

function hookDIOC() {
    var addr = Module.getGlobalExportByName('NtDeviceIoControlFile');
    if (!addr) return;
    Interceptor.attach(addr, {
        onEnter: function(args) {
            this.hFile = args[0];
            this.iosb = args[4];
            this.ioCode = args[5].toInt32() >>> 0;
            this.inBuf = args[6];
            this.inLen = args[7].toInt32();
            this.outBuf = args[8];
            this.outLen = args[9].toInt32();
        },
        onLeave: function(retval) {
            // Only log writes that look like Razer Protocol30 (dsize byte + class byte).
            // Heuristic: input length >= 90, byte[6] in {0x00,0x07,0x0F,0x02,0x03}, byte[5] <= 80
            var rv = retval.toInt32() >>> 0;
            if (this.inLen >= 8) {
                try {
                    var b6 = this.inBuf.add(6).readU8();
                    var b7 = this.inBuf.add(7).readU8();
                    var b5 = this.inBuf.add(5).readU8();
                    var b0 = this.inBuf.add(0).readU8();
                    var b1 = this.inBuf.add(1).readU8();
                    var lookProt30 = (b0 === 0 && b5 <= 80 &&
                        (b6 === 0x00 || b6 === 0x07 || b6 === 0x0F ||
                         b6 === 0x02 || b6 === 0x03 || b6 === 0x04));
                    if (lookProt30 && this.inLen >= 88) {
                        var path = fileMap[this.hFile.toString()] || '<unknown>';
                        send({type:'proto30',
                              ioCode:'0x' + this.ioCode.toString(16),
                              hFile: this.hFile.toString(),
                              path: path,
                              inLen: this.inLen,
                              outLen: this.outLen,
                              ret: '0x' + rv.toString(16),
                              hex: hexify(this.inBuf, Math.min(this.inLen, 100))});
                        return;
                    }
                } catch (e) {}
            }
            // Also log every Razer-class (0x8888) IOCTL with a hint of structure.
            if (((this.ioCode >>> 16) & 0xFFFF) === 0x8888) {
                var path = fileMap[this.hFile.toString()] || '<unknown>';
                var inHex = this.inLen > 0 ? hexify(this.inBuf, Math.min(this.inLen, 64)) : '';
                var outNow = this.outLen > 0 ? hexify(this.outBuf, Math.min(this.outLen, 80)) : '';
                send({type:'razer',
                      ioCode:'0x' + this.ioCode.toString(16),
                      path: path, inLen: this.inLen, outLen: this.outLen,
                      ret: '0x' + rv.toString(16),
                      hex: inHex, outNow: outNow});
                // For STATUS_PENDING (async) reads, schedule a follow-up read
                // of the output buffer so we capture the completion data.
                // 0x88883018 is the read-event channel — output buffer holds
                // the decoded keyboard event after kernel writes it.
                if ((rv >>> 0) === 0x00000103 && this.outLen > 0) {
                    var ob = this.outBuf;
                    var ol = Math.min(this.outLen, 96);
                    var code = '0x' + this.ioCode.toString(16);
                    var initialOut = outNow;
                    setTimeout(function() {
                        var later = hexify(ob, ol);
                        if (later !== initialOut) {
                            send({type:'razerOutLater', ioCode: code, outLater: later});
                        }
                    }, 100);
                    setTimeout(function() {
                        var later = hexify(ob, ol);
                        if (later !== initialOut) {
                            send({type:'razerOutLater', ioCode: code, outLater: later, late: true});
                        }
                    }, 500);
                }
            }
        }
    });
    send({type:'log', msg:'hooked NtDeviceIoControlFile'});
}

function hookHidD() {
    var mod = Process.findModuleByName('hid.dll');
    if (!mod) {
        send({type:'log', msg:'hid.dll not loaded'});
        return;
    }
    // Unconditional logging of every HidD_xxx that takes a hFile + buffer.
    var apis = [
        'HidD_SetFeature', 'HidD_GetFeature',
        'HidD_SetOutputReport', 'HidD_GetInputReport',
        'HidD_SetConfiguration', 'HidD_FlushQueue',
    ];
    var hooked = [];
    apis.forEach(function(name) {
        var fn = mod.findExportByName(name);
        if (!fn) return;
        Interceptor.attach(fn, {
            onEnter: function(a) {
                this.fnName = name;
                this.hFile = a[0];
                this.buf = a[1];
                this.len = a[2] ? a[2].toInt32() : 0;
            },
            onLeave: function(retval) {
                var path = fileMap[this.hFile.toString()] || '<unknown>';
                // Filter to dongle path only (PID_009C MI_00) so we don't drown
                // in all-system HID traffic.
                if (path === '<unknown>' || path.toLowerCase().indexOf('pid_009c') >= 0) {
                    send({type:'hidd_any', fn: this.fnName,
                          hFile: this.hFile.toString(), path: path,
                          len: this.len, ret: retval.toInt32(),
                          hex: hexify(this.buf, Math.min(this.len, 100))});
                }
            }
        });
        hooked.push(name);
    });
    send({type:'log', msg:'hooked hid: ' + hooked.join(',')});
}

hookCreate('NtCreateFile');
hookCreate('NtOpenFile');
hookClose();
hookDIOC();
hookHidD();
send({type:'log', msg:'all hooks installed'});
"""

def main():
    device = frida.get_local_device()
    start = time.time()
    attached = set()
    sessions = []
    lock = threading.Lock()
    seen_proto30 = []   # store first few full payloads

    def on_message(pid, message, data):
        with lock:
            ts = time.time() - start
            if message['type'] != 'send':
                if message['type'] == 'error':
                    print(f"[{ts:7.2f}] [{pid}] ERR: {message}", flush=True)
                return
            p = message['payload']
            t = p.get('type')
            if t == 'proto30':
                print(f"\n[{ts:7.2f}] [{pid}] *** PROTOCOL30 *** ioctl={p['ioCode']} ret={p['ret']} inLen={p['inLen']} outLen={p['outLen']}", flush=True)
                print(f"             path: {p['path']}", flush=True)
                print(f"             hex:  {p['hex']}", flush=True)
            elif t == 'razer':
                print(f"[{ts:7.2f}] [{pid}] razer ioctl={p['ioCode']} ret={p['ret']} inLen={p['inLen']} outLen={p['outLen']} path={p['path']}", flush=True)
                if p.get('hex'): print(f"             in:  {p['hex']}", flush=True)
                if p.get('outNow'): print(f"             out: {p['outNow']}", flush=True)
            elif t == 'razerOutLater':
                tag = " (late)" if p.get('late') else ""
                print(f"[{ts:7.2f}] [{pid}] razer{tag} ioctl={p['ioCode']} OUT-COMPLETED:", flush=True)
                print(f"             {p['outLater']}", flush=True)
            elif t == 'hidd':
                print(f"\n[{ts:7.2f}] [{pid}] *** HidD_SetFeature *** ret={p['ret']} len={p['len']}", flush=True)
                print(f"             path: {p['path']}", flush=True)
                print(f"             hex:  {p['hex']}", flush=True)
            elif t == 'hidd_any':
                print(f"[{ts:7.2f}] [{pid}] {p['fn']} ret={p['ret']} len={p['len']} path_short={p['path'][-40:]}", flush=True)
                if p.get('hex'):
                    print(f"             hex:  {p['hex']}", flush=True)
            elif t == 'log':
                print(f"[{ts:7.2f}] [{pid}] {p['msg']}", flush=True)

    def mk_handler(pid):
        return lambda m, d: on_message(pid, m, d)

    TARGET_NAMES = ('RazerAppEngine.exe', 'razer_elevation_service.exe', 'Razer Synapse Service.exe', 'Razer Central Service.exe')
    print(f"Hooking {TARGET_NAMES} ... change Joro lighting in Synapse, then Ctrl+C", flush=True)
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
                            pass  # silently retry
            time.sleep(0.2)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
        for pid, s, scr in sessions:
            try: scr.unload(); s.detach()
            except: pass


if __name__ == "__main__":
    main()
