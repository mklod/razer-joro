#!/usr/bin/env python3
"""Attach Frida to all Razer processes at once and log hook output.

Runs until Ctrl+C or duration elapses. Logs every hook message with the
source PID so we can tell which process actually made the BLE write.
"""
import sys
import time
import threading
import frida

TARGET_NAMES = (
    'razer_elevation_service.exe',
    'RazerAppEngine.exe',
    'GameManagerService3.exe',
    'RzBTLEManager.exe',
    'RzDeviceManager.exe',
    'RzDeviceManagerEx.exe',
    'RzEngineMon.exe',
)

SCRIPT_PATH = sys.argv[1] if len(sys.argv) > 1 else 'frida_ble_hook.js'

with open(SCRIPT_PATH) as f:
    script_src = f.read()

sessions = []
lock = threading.Lock()

def on_message(pid, name, message, data):
    with lock:
        if message['type'] == 'send':
            print(f"[{name}:{pid}] {message['payload']}", flush=True)
        elif message['type'] == 'error':
            print(f"[{name}:{pid} ERR] {message.get('description')}", flush=True)

def log_console(pid, name, msg):
    with lock:
        for line in msg.splitlines():
            print(f"[{name}:{pid}] {line}", flush=True)

# Enumerate all processes matching target names
device = frida.get_local_device()
procs = device.enumerate_processes()
targets = [p for p in procs if p.name in TARGET_NAMES]
print(f"Found {len(targets)} target processes:", flush=True)
for p in targets:
    print(f"  {p.pid:>6}  {p.name}", flush=True)

if not targets:
    print("No targets — exiting.")
    sys.exit(1)

# Attach to each
for p in targets:
    try:
        session = frida.attach(p.pid)
        script = session.create_script(script_src)
        # Capture console.log output via the script's exports/send mechanism
        # Frida's console.log maps to a log event, not a send, so we override:
        def make_handler(pid=p.pid, name=p.name):
            def handler(message, data):
                if message['type'] == 'log':
                    log_console(pid, name, message['payload'])
                else:
                    on_message(pid, name, message, data)
            return handler
        script.on('message', make_handler())
        script.load()
        sessions.append((p.pid, p.name, session, script))
        print(f"[+] Attached to {p.name} (pid {p.pid})", flush=True)
    except Exception as e:
        print(f"[-] Attach {p.name} (pid {p.pid}) failed: {e}", flush=True)

print(f"\n[*] Hooked {len(sessions)} processes. Ctrl+C to stop.", flush=True)

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("\n[*] Detaching...", flush=True)
    for pid, name, session, script in sessions:
        try:
            script.unload()
            session.detach()
        except Exception:
            pass
