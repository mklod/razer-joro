#!/usr/bin/env python3
"""Hook Windows HID.DLL feature report functions AND the low-level NtDeviceIoControlFile /
NtWriteFile syscalls in ntdll.dll. This catches BOTH Win32 HID API calls and the
bottom-of-stack syscalls so we can't miss a write to any HID/BLE device."""
import sys, frida, time, threading

pid = int(sys.argv[1])

script_src = r"""
function hookExport(modName, fnName, argsToRead) {
    var mod = Process.findModuleByName(modName);
    if (!mod) { send({ type: 'miss_mod', mod: modName }); return; }
    var addr = null;
    mod.enumerateExports().forEach(function(e) { if (e.name === fnName) addr = e.address; });
    if (!addr) { send({ type: 'miss_fn', mod: modName, fn: fnName }); return; }
    try {
        Interceptor.attach(addr, {
            onEnter: function(args) {
                this.fn = fnName;
                this.mod = modName;
                // capture raw args as hex
                this.argv = [];
                for (var i = 0; i < 6; i++) this.argv.push(args[i].toString());
                // grab the data pointer + length per call type
                if (argsToRead === 'SetFeature' || argsToRead === 'SetOutputReport' || argsToRead === 'GetFeature' || argsToRead === 'GetInputReport') {
                    // HidD_SetFeature(HANDLE, PVOID buf, ULONG len)
                    this.buf = args[1];
                    this.len = args[2].toInt32();
                } else if (argsToRead === 'NtDIOCF') {
                    // NtDeviceIoControlFile(FileHandle, Event, ApcRoutine, ApcContext, IoStatusBlock, IoControlCode, InputBuffer, InputBufferLength, OutputBuffer, OutputBufferLength)
                    this.hFile = args[0];
                    this.ioCode = args[5].toInt32();
                    this.buf = args[6];
                    this.len = args[7].toInt32();
                    this.outBuf = args[8];
                    this.outLen = args[9].toInt32();
                } else if (argsToRead === 'NtWriteFile') {
                    // NtWriteFile(FileHandle, Event, ApcRoutine, ApcContext, IoStatusBlock, Buffer, Length, ByteOffset, Key)
                    this.hFile = args[0];
                    this.buf = args[5];
                    this.len = args[6].toInt32();
                }
            },
            onLeave: function(retval) {
                try {
                    var hex = null;
                    if (this.buf && this.len > 0 && this.len < 2048) {
                        try {
                            var b = Memory.readByteArray(this.buf, this.len);
                            var u8 = new Uint8Array(b);
                            var parts = [];
                            for (var i = 0; i < u8.length; i++) parts.push(('00' + u8[i].toString(16)).slice(-2));
                            hex = parts.join(' ');
                        } catch (e) {}
                    }
                    // For NtDIOCF, filter noise: only IO codes that look like bluetooth or HID range
                    if (argsToRead === 'NtDIOCF') {
                        var code = this.ioCode >>> 0;  // unsigned
                        var deviceType = (code >>> 16) & 0xFFFF;
                        if (!(deviceType === 0x41 || deviceType === 0x22 || deviceType === 0x88 || deviceType === 0x8888)) return;
                        var self = this;
                        function hexify(ptr, len) {
                            if (!ptr || len <= 0 || len > 4096) return null;
                            try {
                                var b = ptr.readByteArray(len);
                                if (!b) return '(null)';
                                var u8 = new Uint8Array(b);
                                var p = [];
                                for (var i = 0; i < u8.length; i++) p.push(('00' + u8[i].toString(16)).slice(-2));
                                return p.join(' ');
                            } catch (e) { return 'READ_ERR:' + e.toString(); }
                        }
                        // Read input NOW (it was passed before the call)
                        var inHex = hexify(self.buf, self.len);
                        // Read output TWICE: immediately + delayed 100ms for async fills
                        var outHexNow = hexify(self.outBuf, self.outLen);
                        send({
                            type: 'call',
                            fn: self.fn,
                            ioCode: '0x' + code.toString(16),
                            hFile: self.hFile.toString(),
                            outBufAddr: self.outBuf.toString(),
                            inLen: self.len,
                            inHex: inHex,
                            outLen: self.outLen,
                            outHexNow: outHexNow,
                            retval: retval.toString(),
                        });
                        // Schedule delayed read
                        setTimeout(function() {
                            var outHexLater = hexify(self.outBuf, self.outLen);
                            send({ type: 'delayed_out', ioCode: '0x' + code.toString(16), outHexLater: outHexLater });
                        }, 150);
                        return;
                    }
                    if (argsToRead === 'NtWriteFile') {
                        // Filter: only log if buffer looks HID-ish or small enough to be interesting
                        if (this.len === 0 || this.len > 512) return;
                    }
                    send({ type: 'call', fn: this.fn, len: this.len, hex: hex, retval: retval.toString(), argv: this.argv });
                } catch (e) { send({ type: 'err', where: this.fn, err: e.toString() }); }
            }
        });
        send({ type: 'hooked', mod: modName, fn: fnName, addr: addr.toString() });
    } catch (e) { send({ type: 'err', where: fnName, err: e.toString() }); }
}

// HID.DLL feature and output report APIs
hookExport('HID.DLL', 'HidD_SetFeature', 'SetFeature');
hookExport('HID.DLL', 'HidD_GetFeature', 'GetFeature');
hookExport('HID.DLL', 'HidD_SetOutputReport', 'SetOutputReport');
hookExport('HID.DLL', 'HidD_GetInputReport', 'GetInputReport');

// ntdll syscall wrappers — catches ALL device I/O at the bottom
hookExport('ntdll.dll', 'NtDeviceIoControlFile', 'NtDIOCF');
// NtWriteFile is too noisy, skip unless the above doesn't catch anything
// hookExport('ntdll.dll', 'NtWriteFile', 'NtWriteFile');
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
                if 'ioCode' in p:
                    print(f"[{ts:8.3f}] {p['fn']}  ioCode={p['ioCode']}  hFile={p['hFile']}  inLen={p['inLen']}  outLen={p['outLen']}  ret={p['retval']}", flush=True)
                    if p.get('inHex') is not None: print(f"             in:  {p['inHex']}", flush=True)
                    if p.get('outHexNow') is not None: print(f"             out(now):   {p['outHexNow']}", flush=True)
                else:
                    print(f"[{ts:8.3f}] {p['fn']}  len={p['len']}  ret={p['retval']}", flush=True)
                    if p.get('hex') is not None: print(f"             hex: {p['hex']}", flush=True)
            elif t == 'delayed_out':
                if p.get('outHexLater') is not None:
                    print(f"[{ts:8.3f}]   delayed out for {p['ioCode']}: {p['outHexLater']}", flush=True)
            elif t == 'hooked':
                print(f"[{ts:8.3f}] HOOKED {p['mod']}!{p['fn']} @ {p['addr']}", flush=True)
            elif t == 'miss_mod':
                print(f"[{ts:8.3f}] MISS_MOD {p['mod']}", flush=True)
            elif t == 'miss_fn':
                print(f"[{ts:8.3f}] MISS_FN {p['mod']}!{p['fn']}", flush=True)
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
