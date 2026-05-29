#!/usr/bin/env python3
"""Decode the 0x88883020 event delivery channel of RzDev_02ce.

Previous capture showed every 0x88883020 call has inLen=32 with 20 bytes of
subscription data + 12 trailing bytes of `aa aa aa ...`. The driver likely
writes the event payload into those trailing bytes on IRP completion. This
script polls the INPUT buffer of every outstanding 0x88883020 IRP after
onEnter and reports any diff from the initial contents.

Also hooks user32!SendInput to catch the re-emission path so we can correlate
"event N arrived in buffer X" with "VK Y was posted".

Run: start this, then start Synapse (auto-opens Joro page + posts subscriptions).
Then press F5/F6/F7/F8/F9/F10/F11/F12 one at a time and note the deltas.
"""
import sys, time, frida, threading

SCRIPT = r"""
// Decode the 0x88883020 event-delivery channel.
//
// Strategy: for each 0x88883020 call that returns STATUS_PENDING, we capture
// the IoStatusBlock pointer (arg 4). The kernel writes Status + Information
// into that struct on IRP completion. We poll it and, when Status changes
// from 0x103 to something else, print the full state including the input
// buffer contents (the driver may also write into the SystemBuffer; after
// completion the SystemBuffer is copied back to UserOutput, which is null
// here, but we can also read the user InputBuffer in case the driver uses
// it). We also log: Event handle (arg 1), ApcRoutine (arg 2), and hook
// user32!SendInput so we can correlate completion -> VK re-emission.

var pending = {};  // key -> {iosb, inBuf, inLen, hFile, eventH, apc, seq, start}
var seq = 0;

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

function hookNtDIOCF() {
    var addr = Module.getGlobalExportByName('NtDeviceIoControlFile');
    if (!addr) { send({type:'err', msg:'NtDIOCF not found'}); return; }
    Interceptor.attach(addr, {
        onEnter: function(args) {
            this.hFile = args[0];
            this.eventH = args[1];
            this.apcRoutine = args[2];
            this.apcContext = args[3];
            this.iosb = args[4];
            this.ioCode = args[5].toInt32() >>> 0;
            this.inBuf = args[6];
            this.inLen = args[7].toInt32();
            this.outBuf = args[8];
            this.outLen = args[9].toInt32();
            this.is20 = (this.ioCode === 0x88883020);
            this.is24 = (this.ioCode === 0x88883024);
            // Razer device type = top 16 bits == 0x8888
            this.isRazer = ((this.ioCode >>> 16) & 0xFFFF) === 0x8888;
        },
        onLeave: function(retval) {
            var rv = retval.toInt32();
            if (this.is20) {
                seq++;
                var inHex = hexify(this.inBuf, this.inLen);
                send({type:'post20', seq:seq, hFile:this.hFile.toString(),
                      eventH:this.eventH.toString(), apcRoutine:this.apcRoutine.toString(),
                      iosb:this.iosb.toString(), inLen:this.inLen, outLen:this.outLen,
                      ret:'0x'+(rv>>>0).toString(16), inHex:inHex});
                if ((rv >>> 0) === 0x00000103) {
                    var key = 's' + seq;
                    // Read initial IOSB bytes so we can diff later.
                    var iosbInit = hexify(this.iosb, 16);
                    pending[key] = {
                        iosb: this.iosb, inBuf: this.inBuf, inLen: this.inLen,
                        hFile: this.hFile.toString(), eventH: this.eventH.toString(),
                        apc: this.apcRoutine.toString(),
                        iosbInit: iosbInit, inInit: inHex,
                        seq: seq, start: Date.now(),
                    };
                }
            } else if (this.is24) {
                send({type:'sethook', ret:'0x'+(rv>>>0).toString(16),
                      inLen:this.inLen,
                      inHex:hexify(this.inBuf, this.inLen)});
            } else if (this.isRazer) {
                // Generic 0x8888 IOCTL — log code, in, and (deferred) out.
                var inHex = this.inLen > 0 ? hexify(this.inBuf, Math.min(this.inLen, 64)) : null;
                var outNow = this.outLen > 0 ? hexify(this.outBuf, Math.min(this.outLen, 64)) : null;
                send({type:'generic', ioCode:'0x'+(this.ioCode>>>0).toString(16),
                      hFile:this.hFile.toString(), inLen:this.inLen, outLen:this.outLen,
                      ret:'0x'+(rv>>>0).toString(16), inHex:inHex, outNow:outNow});
                // If STATUS_PENDING, schedule a later read of out buffer to catch
                // async completions that write into the OUTPUT buffer.
                if ((rv >>> 0) === 0x00000103 && this.outLen > 0) {
                    var ob = this.outBuf;
                    var ol = Math.min(this.outLen, 64);
                    var code = '0x'+(this.ioCode>>>0).toString(16);
                    var initialOut = outNow;
                    setTimeout(function() {
                        var later = hexify(ob, ol);
                        if (later !== initialOut) {
                            send({type:'genericOutLater', ioCode:code, outLater:later});
                        }
                    }, 200);
                }
            }
        }
    });
    send({type:'hooked', name:'NtDeviceIoControlFile'});
}

function hookSendInput() {
    var mod = Process.findModuleByName('user32.dll');
    if (!mod) { send({type:'err', msg:'user32.dll not loaded'}); return false; }
    var fn = mod.findExportByName('SendInput');
    if (!fn) { send({type:'err', msg:'SendInput export not found'}); return false; }
    Interceptor.attach(fn, {
        onEnter: function(args) {
            var cInputs = args[0].toInt32();
            var pInputs = args[1];
            var cbSize = args[2].toInt32();
            var dump = [];
            for (var i = 0; i < Math.min(cInputs, 8); i++) {
                var base = pInputs.add(i * cbSize);
                try {
                    var type = base.readU32();
                    if (type === 1) {
                        var vk = base.add(8).readU16();
                        var sc = base.add(10).readU16();
                        var flags = base.add(12).readU32();
                        dump.push({vk:vk, sc:sc, flags:flags});
                    } else {
                        dump.push({type:type});
                    }
                } catch (e) { dump.push({err:e.toString()}); }
            }
            send({type:'sendinput', n:cInputs, items:dump});
        }
    });
    send({type:'hooked', name:'user32!SendInput'});
    return true;
}

// Poll each pending IOSB for completion (Status field flips from 0x103).
setInterval(function() {
    var now = Date.now();
    var toDelete = [];
    for (var k in pending) {
        var p = pending[k];
        try {
            // IO_STATUS_BLOCK on x64: union { NTSTATUS Status; PVOID Pointer; } (8B)
            // then ULONG_PTR Information (8B). The first 4 bytes are the Status.
            var status = p.iosb.readU32() >>> 0;
            if (status !== 0x00000103) {
                // Completed. Read full IOSB + current input buffer contents.
                var iosbNow = hexify(p.iosb, 16);
                var inNow = hexify(p.inBuf, p.inLen);
                send({type:'complete', seq:p.seq, hFile:p.hFile,
                      elapsedMs:(now - p.start), status:'0x'+status.toString(16),
                      iosbInit:p.iosbInit, iosbNow:iosbNow,
                      inInit:p.inInit, inNow:inNow});
                toDelete.push(k);
            } else if (now - p.start > 60000) {
                toDelete.push(k);
            }
        } catch (e) { toDelete.push(k); }
    }
    for (var i = 0; i < toDelete.length; i++) delete pending[toDelete[i]];
}, 20);

hookNtDIOCF();
if (!hookSendInput()) {
    var tries = 0;
    var t = setInterval(function() {
        tries++;
        if (hookSendInput() || tries > 60) clearInterval(t);
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
        if message['type'] != 'send':
            if message['type'] == 'error':
                print(f"[{ts:7.2f}] [{pid}] SCRIPT ERROR: {message}", flush=True)
            return
        p = message['payload']
        t = p.get('type')
        if t == 'post20':
            print(f"[{ts:7.2f}] [{pid}] POST20 seq={p['seq']} hFile={p['hFile']} iosb={p['iosb']} event={p['eventH']} apc={p['apcRoutine']} ret={p['ret']}", flush=True)
            print(f"             in: {p['inHex']}", flush=True)
        elif t == 'complete':
            print(f"[{ts:7.2f}] [{pid}] *** COMPLETE seq={p['seq']} after {p['elapsedMs']}ms status={p['status']} ***", flush=True)
            print(f"             iosb init: {p['iosbInit']}", flush=True)
            print(f"             iosb now:  {p['iosbNow']}", flush=True)
            print(f"             in init:   {p['inInit']}", flush=True)
            print(f"             in now:    {p['inNow']}", flush=True)
        elif t == 'sethook':
            print(f"[{ts:7.2f}] [{pid}] SetInputHook inLen={p.get('inLen')} ret={p['ret']}", flush=True)
            print(f"             in: {p['inHex']}", flush=True)
        elif t == 'generic':
            print(f"[{ts:7.2f}] [{pid}] IOCTL {p['ioCode']} hFile={p['hFile']} inLen={p['inLen']} outLen={p['outLen']} ret={p['ret']}", flush=True)
            if p.get('inHex'): print(f"             in:  {p['inHex']}", flush=True)
            if p.get('outNow'): print(f"             out: {p['outNow']}", flush=True)
        elif t == 'genericOutLater':
            print(f"[{ts:7.2f}] [{pid}] LATER {p['ioCode']} out: {p['outLater']}", flush=True)
        elif t == 'sendinput':
            items = p['items']
            desc = ', '.join([f"vk=0x{it.get('vk',0):02x} sc=0x{it.get('sc',0):02x} f=0x{it.get('flags',0):x}" if 'vk' in it else str(it) for it in items])
            print(f"[{ts:7.2f}] [{pid}] SendInput n={p['n']}: {desc}", flush=True)
        elif t == 'hooked':
            print(f"[{ts:7.2f}] [{pid}] HOOKED {p['name']}", flush=True)
        elif t == 'err':
            print(f"[{ts:7.2f}] [{pid}] ERR {p['msg']}", flush=True)

def mk_handler(pid):
    return lambda m, d: on_message(pid, m, d)

TARGET_NAMES = ('RazerAppEngine.exe', 'razer_elevation_service.exe', 'Razer Synapse Service.exe', 'Razer Central Service.exe')
print(f"Waiting for {TARGET_NAMES}... Ctrl+C to stop", flush=True)
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
                    print(f"[{time.time()-start:7.2f}] [+] Attached {p.pid}", flush=True)
                except Exception as e:
                    if 'frida-agent' not in str(e):
                        print(f"[{time.time()-start:7.2f}] [-] {p.pid}: {e}", flush=True)
        time.sleep(0.1)
except KeyboardInterrupt:
    for pid, s, scr in sessions:
        try: scr.unload(); s.detach()
        except: pass
