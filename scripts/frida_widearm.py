#!/usr/bin/env python3
"""Wider Frida capture: hooks NtDeviceIoControlFile + NtCreateFile + NtSetValueKey
in RazerAppEngine. Goal: find any kernel state Synapse touches during the Joro
tile click that we're missing in our PoC.
"""
import sys, time, frida, threading

SCRIPT = r"""
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

// Read UNICODE_STRING from kernel-form: { Length:u16, MaximumLength:u16, Buffer:ptr }
function readUString(ptr) {
    if (!ptr || ptr.isNull()) return null;
    try {
        var len = ptr.readU16();
        var buf = ptr.add(8).readPointer();
        if (buf.isNull() || len === 0) return '';
        return buf.readUtf16String(len / 2);
    } catch (e) { return 'ERR:' + e.toString(); }
}

// OBJECT_ATTRIBUTES: { Length, RootDirectory, ObjectName(PUNICODE_STRING), Attributes, SecurityDescriptor, ... }
function readObjectAttrName(ptr) {
    if (!ptr || ptr.isNull()) return null;
    try {
        var nameP = ptr.add(16).readPointer();  // ObjectName at offset 16 on x64
        return readUString(nameP);
    } catch (e) { return 'ERR:' + e.toString(); }
}

function hookNtDIOCF() {
    var addr = Module.getGlobalExportByName('NtDeviceIoControlFile');
    if (!addr) { send({type:'err', msg:'NtDIOCF not found'}); return; }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            this.ioCode = args[5].toInt32() >>> 0;
            this.inLen = args[7].toInt32();
            this.outLen = args[9].toInt32();
            this.inBuf = args[6];
            this.outBuf = args[8];
            this.hFile = args[0];
            // Log every IOCTL so we can see device-type variety
            this.deviceType = (this.ioCode >>> 16) & 0xFFFF;
        },
        onLeave: function(retval) {
            var rv = retval.toInt32();
            send({
                type:'ioctl',
                ioCode: '0x' + this.ioCode.toString(16),
                deviceType: '0x' + this.deviceType.toString(16),
                hFile: this.hFile.toString(),
                inLen: this.inLen, outLen: this.outLen,
                ret: '0x' + (rv>>>0).toString(16),
                inHex: this.inLen > 0 ? hexify(this.inBuf, Math.min(this.inLen, 256)) : null,
            });
        }
    });
    send({type:'hooked', name:'NtDeviceIoControlFile'});
}

function hookNtCreateFile() {
    var addr = Module.getGlobalExportByName('NtCreateFile');
    if (!addr) { send({type:'err', msg:'NtCreateFile not found'}); return; }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            // args: FileHandle*, DesiredAccess, ObjectAttributes*, IoStatusBlock*,
            //       AllocationSize*, FileAttributes, ShareAccess, CreateDisposition,
            //       CreateOptions, EaBuffer*, EaLength
            this.pFileHandle = args[0];
            this.objAttrs = args[2];
        },
        onLeave: function(retval) {
            var name = readObjectAttrName(this.objAttrs);
            if (!name) return;
            // Skip noise: files under C:\, registry, local pipes that aren't razer/bth/hid
            var lc = name.toLowerCase();
            var isDevice = lc.indexOf('\\??\\') === 0 || lc.indexOf('\\device\\') === 0;
            if (!isDevice) return;
            // Log all \Device\ + \??\ opens — we want full handle→device mapping
            try {
                var handlePtr = this.pFileHandle;
                var hval = handlePtr ? handlePtr.readPointer() : null;
                send({type:'create', name:name, handle:hval?hval.toString():'?',
                      ret:'0x'+(retval.toInt32()>>>0).toString(16)});
            } catch (e) {
                send({type:'create', name:name, handle:'?',
                      ret:'0x'+(retval.toInt32()>>>0).toString(16)});
            }
        }
    });
    send({type:'hooked', name:'NtCreateFile'});
}

function hookNtWriteFile() {
    var addr = Module.getGlobalExportByName('NtWriteFile');
    if (!addr) { send({type:'err', msg:'NtWriteFile not found'}); return; }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            // args: FileHandle, Event, ApcRoutine, ApcContext, IoStatusBlock,
            //       Buffer, Length, ByteOffset, Key
            this.hFile = args[0];
            this.buf = args[5];
            this.len = args[6].toInt32();
        },
        onLeave: function(retval) {
            if (this.len > 0 && this.len < 256) {
                send({type:'write', hFile:this.hFile.toString(), len:this.len,
                      hex:hexify(this.buf, Math.min(this.len, 64)),
                      ret:'0x'+(retval.toInt32()>>>0).toString(16)});
            }
        }
    });
    send({type:'hooked', name:'NtWriteFile'});
}

function hookNtSetValueKey() {
    var addr = Module.getGlobalExportByName('NtSetValueKey');
    if (!addr) { send({type:'err', msg:'NtSetValueKey not found'}); return; }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            // args: KeyHandle, ValueName*, TitleIndex, Type, Data*, DataSize
            this.valueName = readUString(args[1]);
            this.type = args[3].toInt32();
            this.dataSize = args[5].toInt32();
            this.data = args[4];
        },
        onLeave: function(retval) {
            // Filter for Razer-related values only
            if (this.valueName && this.valueName.toLowerCase().indexOf('razer') === -1 && this.valueName.toLowerCase().indexOf('rz') === -1 && this.valueName.toLowerCase().indexOf('joro') === -1 && this.valueName.toLowerCase().indexOf('hook') === -1) {
                return;
            }
            send({type:'setval', name:this.valueName, type_: this.type, size:this.dataSize,
                  data: this.dataSize > 0 && this.dataSize < 256 ? hexify(this.data, this.dataSize) : null,
                  ret:'0x'+(retval.toInt32()>>>0).toString(16)});
        }
    });
    send({type:'hooked', name:'NtSetValueKey'});
}

hookNtDIOCF();
hookNtCreateFile();
hookNtSetValueKey();
hookNtWriteFile();
"""

device = frida.get_local_device()
start = time.time()
attached = set()
sessions = []
lock = threading.Lock()
counts_by_iocode = {}

def on_message(pid, message, data):
    with lock:
        ts = time.time() - start
        if message['type'] != 'send':
            if message['type'] == 'error':
                print(f"[{ts:7.2f}] [{pid}] SCRIPT ERROR: {message}", flush=True)
            return
        p = message['payload']
        t = p.get('type')
        if t == 'ioctl':
            code = p['ioCode']
            counts_by_iocode[code] = counts_by_iocode.get(code, 0) + 1
            # Print 0x8888 (Razer) and a few other interesting device types.
            # Only BLE (0x47) + Razer (0x8888) — filter everything else
            dt = p['deviceType']
            if dt in ('0x47', '0x8888'):
                print(f"[{ts:7.2f}] [{pid}] IOCTL {code} dt={dt} hFile={p['hFile']} inLen={p['inLen']} outLen={p['outLen']} ret={p['ret']}", flush=True)
                if p.get('inHex'): print(f"             in: {p['inHex']}", flush=True)
        elif t == 'create':
            print(f"[{ts:7.2f}] [{pid}] CREATE h={p.get('handle','?')} {p['name']} ret={p['ret']}", flush=True)
        elif t == 'write':
            print(f"[{ts:7.2f}] [{pid}] WRITE hFile={p['hFile']} len={p['len']} ret={p['ret']}", flush=True)
            if p.get('hex'): print(f"             hex: {p['hex']}", flush=True)
        elif t == 'setval':
            print(f"[{ts:7.2f}] [{pid}] SETVAL name={p['name']} type={p['type_']} size={p['size']} data={p.get('data')}", flush=True)
        elif t == 'hooked':
            print(f"[{ts:7.2f}] [{pid}] HOOKED {p['name']}", flush=True)
        elif t == 'err':
            print(f"[{ts:7.2f}] [{pid}] ERR {p['msg']}", flush=True)

def mk_handler(pid):
    return lambda m, d: on_message(pid, m, d)

TARGETS = ('RazerAppEngine.exe', 'razer_elevation_service.exe', 'GameManagerService3.exe', 'RzEngineMon.exe', 'Razer Synapse Service.exe', 'Razer Central Service.exe')
print(f"Waiting for {TARGETS}... Ctrl+C to stop", flush=True)
try:
    while True:
        for p in device.enumerate_processes():
            if p.name in TARGETS and p.pid not in attached:
                attached.add(p.pid)
                try:
                    sess = device.attach(p.pid)
                    scr = sess.create_script(SCRIPT)
                    scr.on('message', mk_handler(p.pid))
                    scr.load()
                    sessions.append((p.pid, sess, scr))
                    print(f"[{time.time()-start:7.2f}] [+] Attached {p.pid}", flush=True)
                except Exception as e:
                    if 'frida-agent' not in str(e):
                        print(f"[{time.time()-start:7.2f}] [-] {p.pid}: {e}", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    with lock:
        print("\n=== IOCTL counts by code ===", flush=True)
        for code, n in sorted(counts_by_iocode.items(), key=lambda x: -x[1]):
            print(f"  {code}: {n}", flush=True)
    for pid, s, scr in sessions:
        try: scr.unload(); s.detach()
        except: pass
