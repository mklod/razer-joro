"""Try to attach to every RazerAppEngine and razer_elevation_service
process, print explicit success/failure for each.
"""
import frida

device = frida.get_local_device()
TARGETS = ('RazerAppEngine.exe', 'razer_elevation_service.exe',
           'Razer Synapse Service.exe', 'Razer Central Service.exe')

procs = [p for p in device.enumerate_processes() if p.name in TARGETS]
print(f"Found {len(procs)} target procs:")
for p in procs:
    print(f"  PID={p.pid} Name={p.name}")
print()
for p in procs:
    try:
        s = device.attach(p.pid)
        print(f"  [OK]   PID={p.pid} attached")
        s.detach()
    except Exception as e:
        print(f"  [FAIL] PID={p.pid} {p.name}: {e}")
