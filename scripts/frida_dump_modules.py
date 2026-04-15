#!/usr/bin/env python3
"""Dump ALL loaded modules in a Frida target, highlighting Razer / .node / BLE ones."""
import sys, frida, time, json
pid = int(sys.argv[1])
device = frida.get_local_device()
sess = device.attach(pid)
script = sess.create_script("""
var all = Process.enumerateModules();
send({ total: all.length, modules: all.map(function(m) { return { name: m.name, path: m.path, size: m.size }; }) });
""")
result = {}
script.on('message', lambda msg, data: result.update(msg['payload']) if msg['type']=='send' else None)
script.load()
time.sleep(1)
script.unload()
sess.detach()

all_mods = result.get('modules', [])
print(f"Total loaded modules: {result.get('total')}")
razer = [m for m in all_mods if '\\Razer' in m['path'] or '\\Chroma' in m['path'] or m['name'].startswith('Rz') or m['name'].startswith('rz_') or m['name'].startswith('razer')]
print(f"\n=== Razer-path modules ({len(razer)}) ===")
for m in razer:
    print(f"  {m['name']:50} {m['path']}")

nodes = [m for m in all_mods if m['name'].endswith('.node')]
print(f"\n=== .node native modules ({len(nodes)}) ===")
for m in nodes:
    print(f"  {m['name']:50} {m['path']}")

ble = [m for m in all_mods if any(k in m['name'].lower() for k in ('bluetooth', 'bth', 'ble', 'gatt'))]
print(f"\n=== Bluetooth-related ({len(ble)}) ===")
for m in ble:
    print(f"  {m['name']:50} {m['path']}")
