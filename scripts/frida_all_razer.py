#!/usr/bin/env python3
"""Mass-hook every export of every Razer-owned DLL + every .tmp.node native
addon loaded in PID 16540. Log function name on each call. Use to spot which
function fires on an fn/mm toggle click after mapping_engine.dll turned up
dry.
"""
import frida, sys, time

SCRIPT = r"""
var targets = [];
var mods = Process.enumerateModules();
for (var i = 0; i < mods.length; i++) {
  var n = mods[i].name;
  var nl = n.toLowerCase();
  // Razer-owned DLLs + native node addons
  if (nl.indexOf('rz') === 0 || nl.indexOf('razer') >= 0 ||
      nl.indexOf('mapping_engine') >= 0 || nl.indexOf('.tmp.node') >= 0) {
    targets.push(mods[i]);
  }
}
var totalHooked = 0;
for (var i = 0; i < targets.length; i++) {
  var mod = targets[i];
  var exp = mod.enumerateExports();
  var hooked = 0;
  for (var j = 0; j < exp.length; j++) {
    // Skip mangled C++ aliases (they duplicate the extern-C forms)
    if (exp[j].name.indexOf('?') === 0) continue;
    if (exp[j].name === 'napi_register_module_v1' || exp[j].name === 'node_api_module_get_api_version_v1') continue;
    (function(modname, name, addr){
      try {
        Interceptor.attach(addr, {
          onEnter: function(args) {
            send({t:'call', mod:modname, fn:name});
          }
        });
        hooked++;
      } catch (e) {}
    })(mod.name, exp[j].name, exp[j].address);
    hooked = hooked;
  }
  send({t:'modready', mod:mod.name, hooked:hooked, total:exp.length});
  totalHooked += hooked;
}
send({t:'done', total:totalHooked, modules:targets.length});
"""

def main():
    dev = frida.get_local_device()
    pid = int(sys.argv[1]) if len(sys.argv) > 1 else 16540
    sess = dev.attach(pid)
    script = sess.create_script(SCRIPT)
    start = time.time()
    def on_msg(m, d):
        if m['type'] != 'send':
            print(f"FRIDA {m}", flush=True); return
        p = m['payload']
        t = p.get('t')
        ts = f"[{time.time()-start:7.2f}]"
        if t == 'call':
            print(f"{ts} {p['mod']}!{p['fn']}", flush=True)
        elif t == 'modready':
            print(f"{ts} MOD {p['mod']} hooked {p['hooked']}/{p['total']}", flush=True)
        elif t == 'done':
            print(f"{ts} DONE total_hooked={p['total']} modules={p['modules']}", flush=True)
    script.on('message', on_msg)
    script.load()
    print("Ctrl+C to stop.", flush=True)
    try:
        while True: time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        try: script.unload(); sess.detach()
        except: pass

if __name__ == '__main__':
    main()
