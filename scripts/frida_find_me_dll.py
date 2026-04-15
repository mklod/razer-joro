#!/usr/bin/env python3
"""Check every RazerAppEngine process to find the one(s) with mapping_engine.dll loaded."""
import sys, frida, time

TARGET_MODULE = 'mapping_engine.dll'

device = frida.get_local_device()
procs = [p for p in device.enumerate_processes() if p.name == 'RazerAppEngine.exe']
print(f"Checking {len(procs)} RazerAppEngine processes...", flush=True)

script_src = """
var m = Process.findModuleByName('mapping_engine.dll');
send({ hasIt: m !== null, moduleCount: Process.enumerateModules().length });
"""

found = []
for p in procs:
    try:
        sess = device.attach(p.pid)
        script = sess.create_script(script_src)
        result = {'got': False}
        def on_msg(msg, data, r=result):
            if msg['type'] == 'send':
                r['payload'] = msg['payload']
                r['got'] = True
        script.on('message', on_msg)
        script.load()
        time.sleep(0.3)
        if result['got']:
            p2 = result['payload']
            if p2['hasIt']:
                print(f"  [{p.pid}] YES modules={p2['moduleCount']}", flush=True)
                found.append(p.pid)
            else:
                print(f"  [{p.pid}] no ({p2['moduleCount']} modules)", flush=True)
        else:
            print(f"  [{p.pid}] no response", flush=True)
        script.unload()
        sess.detach()
    except Exception as e:
        msg = str(e)
        if 'frida-agent' in msg:
            print(f"  [{p.pid}] sandboxed", flush=True)
        else:
            print(f"  [{p.pid}] err: {msg}", flush=True)

print(f"\nFound mapping_engine.dll in: {found}", flush=True)
