#!/usr/bin/env python3
"""Hook razer_elevation_service.exe (or any specified PID) — log EVERY
NtDeviceIoControlFile + NtWriteFile + NtFsControlFile call with full hex.
Only kbd-class (dt=0x16) and disk (dt=0x4) IOCTLs are filtered out.

Use: python frida_elev_toggle.py [PID]
Then toggle fn/mm in Synapse UI. Ctrl+C to stop.
"""
import frida, sys, time

SCRIPT = r"""
function hexify(p, l) {
  if (!p || l <= 0 || l > 4096) return null;
  try {
    var b = p.readByteArray(l);
    if (!b) return null;
    var u = new Uint8Array(b);
    var h = [];
    for (var i = 0; i < u.length; i++) h.push(('00'+u[i].toString(16)).slice(-2));
    return h.join(' ');
  } catch (e) { return 'ERR:'+e; }
}

function hookDIOCF() {
  var fn = Module.getGlobalExportByName('NtDeviceIoControlFile');
  Interceptor.attach(fn, {
    onEnter: function(args) {
      this.h = args[0]; this.code = args[5].toInt32() >>> 0;
      this.inB = args[6]; this.inL = args[7].toInt32();
    },
    onLeave: function(rv) {
      var dt = (this.code >>> 16) & 0xFFFF;
      // skip kbd class (0x16) and disk (0x4) noise
      if (dt === 0x16 || dt === 0x4) return;
      var hex = this.inL > 0 ? hexify(this.inB, Math.min(this.inL, 256)) : null;
      send({t:'ioctl', code:'0x'+this.code.toString(16), dt:'0x'+dt.toString(16),
            h:this.h.toString(), inL:this.inL,
            ret:'0x'+(rv.toInt32()>>>0).toString(16), hex:hex});
    }
  });
  send({t:'ok', msg:'hooked NtDeviceIoControlFile'});
}

function hookWrite() {
  var fn = Module.getGlobalExportByName('NtWriteFile');
  Interceptor.attach(fn, {
    onEnter: function(args) { this.h = args[0]; this.buf = args[5]; this.len = args[6].toInt32(); },
    onLeave: function(rv) {
      if (this.len <= 0 || this.len > 4096) return;
      var hex = hexify(this.buf, Math.min(this.len, 256));
      send({t:'write', h:this.h.toString(), len:this.len,
            ret:'0x'+(rv.toInt32()>>>0).toString(16), hex:hex});
    }
  });
  send({t:'ok', msg:'hooked NtWriteFile'});
}

function hookFsctl() {
  var fn = Module.getGlobalExportByName('NtFsControlFile');
  if (!fn) { send({t:'err', msg:'no NtFsControlFile'}); return; }
  Interceptor.attach(fn, {
    onEnter: function(args) { this.h = args[0]; this.code = args[5].toInt32() >>> 0;
                              this.inB = args[6]; this.inL = args[7].toInt32(); },
    onLeave: function(rv) {
      var hex = this.inL > 0 && this.inL < 256 ? hexify(this.inB, this.inL) : null;
      send({t:'fsctl', code:'0x'+this.code.toString(16),
            h:this.h.toString(), inL:this.inL,
            ret:'0x'+(rv.toInt32()>>>0).toString(16), hex:hex});
    }
  });
  send({t:'ok', msg:'hooked NtFsControlFile'});
}

hookDIOCF();
hookWrite();
hookFsctl();
"""

def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 1740
    dev = frida.get_local_device()
    sess = dev.attach(pid)
    script = sess.create_script(SCRIPT)
    start = time.time()

    def on_msg(m, d):
        if m['type'] != 'send':
            print(f"[{time.time()-start:7.2f}] FRIDA {m}", flush=True)
            return
        p = m['payload']
        t = p.get('t')
        ts = f"[{time.time()-start:7.2f}]"
        if t == 'ok':
            print(f"{ts} OK {p['msg']}", flush=True)
        elif t == 'err':
            print(f"{ts} ERR {p['msg']}", flush=True)
        elif t == 'ioctl':
            print(f"{ts} IOCTL {p['code']} dt={p['dt']} h={p['h']} inL={p['inL']} ret={p['ret']}", flush=True)
            if p.get('hex'): print(f"        {p['hex']}", flush=True)
        elif t == 'write':
            print(f"{ts} WRITE h={p['h']} len={p['len']} ret={p['ret']}", flush=True)
            if p.get('hex'): print(f"        {p['hex']}", flush=True)
        elif t == 'fsctl':
            print(f"{ts} FSCTL {p['code']} h={p['h']} inL={p['inL']} ret={p['ret']}", flush=True)
            if p.get('hex'): print(f"        {p['hex']}", flush=True)

    script.on('message', on_msg)
    script.load()
    print(f"PID={pid} hooked. Ctrl+C to stop.", flush=True)
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try: script.unload(); sess.detach()
        except: pass

if __name__ == '__main__':
    main()
