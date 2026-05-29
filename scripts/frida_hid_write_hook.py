#!/usr/bin/env python3
"""Hook node-hid's hid_write in RazerAppEngine (PID 16540) and log every call
with full hex payload. User then clicks the fn-mm toggle once, and we capture
the exact HID report Synapse sends.

Signature (node-hid): int hid_write(hid_device *dev, const unsigned char *data, size_t length)
"""
import frida, sys, time, threading

TARGET_MODULE = '6392425c-85e5-48ec-95c3-c0883feb5573.tmp.node'

SCRIPT = """
var modName = '%s';
var mod = Process.findModuleByName(modName);
if (!mod) { send({type:'err', msg:'module not loaded: '+modName}); }
else {
  function hookBufFn(name) {
    var fn = mod.findExportByName(name);
    if (!fn) { send({type:'err', msg:'no export '+name}); return; }
    send({type:'ok', msg:'hooking '+name+' @ '+fn});
    Interceptor.attach(fn, {
      onEnter: function(args) { this.fn = name; this.dev = args[0]; this.buf = args[1]; this.len = args[2].toInt32(); },
      onLeave: function(retval) {
        var len = this.len;
        var hex = null;
        if (len > 0 && len < 4096) {
          var b = this.buf.readByteArray(len);
          var u8 = new Uint8Array(b);
          var hp = [];
          for (var i = 0; i < u8.length; i++) hp.push(('00'+u8[i].toString(16)).slice(-2));
          hex = hp.join(' ');
        }
        send({type:'call', fn:this.fn, len:len, dev:this.dev.toString(), ret:retval.toInt32(), hex:hex});
      }
    });
  }
  hookBufFn('hid_write');
  hookBufFn('hid_send_feature_report');
  hookBufFn('hid_get_feature_report');
  hookBufFn('hid_get_input_report');

  // hid_open_path: const char *path -> device*
  var op = mod.findExportByName('hid_open_path');
  if (op) {
    send({type:'ok', msg:'hooking hid_open_path'});
    Interceptor.attach(op, {
      onEnter: function(args) { try { this.path = args[0].readUtf8String(); } catch (e) { this.path = '(err)'; } },
      onLeave: function(retval) { send({type:'open', path:this.path, dev:retval.toString()}); }
    });
  }
}
""" % (TARGET_MODULE,)

def main():
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 16540
    dev = frida.get_local_device()
    sess = dev.attach(pid)
    script = sess.create_script(SCRIPT)
    start = time.time()
    def on_msg(m, d):
        if m['type'] != 'send':
            print(f"[{time.time()-start:7.2f}] ERR {m}", flush=True)
            return
        p = m['payload']
        t = p.get('type')
        if t == 'ok':
            print(f"[{time.time()-start:7.2f}] OK {p['msg']}", flush=True)
        elif t == 'err':
            print(f"[{time.time()-start:7.2f}] ERR {p['msg']}", flush=True)
        elif t == 'call':
            print(f"[{time.time()-start:7.2f}] {p['fn']} dev={p['dev']} len={p['len']} ret={p['ret']}", flush=True)
            if p.get('hex'):
                print(f"             {p['hex']}", flush=True)
        elif t == 'open':
            print(f"[{time.time()-start:7.2f}] hid_open_path dev={p['dev']} path={p['path']}", flush=True)
    script.on('message', on_msg)
    script.load()
    print(f"Hooked. Press Ctrl+C to stop. Target PID={pid}", flush=True)
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try: script.unload(); sess.detach()
        except: pass

if __name__ == '__main__':
    main()
