#!/usr/bin/env python3
"""Dump exports of each .node native module in a Frida target."""
import sys, frida, time, json
pid = int(sys.argv[1])
device = frida.get_local_device()
sess = device.attach(pid)
script = sess.create_script(r"""
var all = Process.enumerateModules();
var nodes = all.filter(function(m) { return m.name.endsWith('.node'); });
send({ total: nodes.length });
nodes.forEach(function(m) {
    try {
        var exp = m.enumerateExports();
        var filt = exp.filter(function(e) {
            return /bluetooth|bth|ble|gatt|write|rz|razer|device|conn/i.test(e.name);
        });
        send({
            name: m.name,
            path: m.path,
            size: m.size,
            totalExports: exp.length,
            interesting: filt.slice(0, 40).map(function(e) { return e.name; }),
        });
    } catch (e) { send({ name: m.name, err: e.toString() }); }
});
""")
results = []
script.on('message', lambda msg, data: results.append(msg['payload']) if msg['type']=='send' else print(msg))
script.load()
time.sleep(2)
script.unload()
sess.detach()

for r in results:
    if 'total' in r and len(r) == 1:
        print(f"Total .node modules: {r['total']}\n")
        continue
    print(f"=== {r.get('name')} ===")
    print(f"  path: {r.get('path')}")
    print(f"  size: {r.get('size')} totalExports: {r.get('totalExports')}")
    interesting = r.get('interesting', [])
    if interesting:
        print(f"  interesting ({len(interesting)}):")
        for n in interesting: print(f"    {n}")
    print()
