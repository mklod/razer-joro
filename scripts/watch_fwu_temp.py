"""
Snapshot %TEMP% before running the FW updater, run it, capture every file
that appears, copy them to a safe directory before the wrapper cleans up.

Usage:
  1. python watch_fwu_temp.py monitor      # starts polling in foreground
  2. (in another shell) launch the wrapper EXE
  3. Wait for completion. Watcher saves all new files to OUT_DIR.
  4. Ctrl+C to stop the watcher.
"""
import os, time, shutil, sys
from pathlib import Path

OUT_DIR = Path(r'L:\PROJECTS\razer-joro\captures\fwu_temp_capture')
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Monitor multiple temp roots
ROOTS = [
    Path(os.environ.get('TEMP', r'C:\Users\mklod\AppData\Local\Temp')),
    Path(r'C:\Windows\Temp'),
    Path(r'C:\ProgramData\Razer'),
]

# Build initial snapshot of every file in those roots
def snapshot(root):
    out = {}
    if not root.exists(): return out
    try:
        for p in root.rglob('*'):
            try:
                if p.is_file():
                    out[str(p)] = (p.stat().st_size, p.stat().st_mtime)
            except (PermissionError, OSError):
                continue
    except (PermissionError, OSError):
        pass
    return out

print("Building initial snapshot of TEMP directories...")
baseline = {}
for r in ROOTS:
    baseline[r] = snapshot(r)
    print(f"  {r}: {len(baseline[r])} files")

print("\n  Run the wrapper EXE NOW: Joro_02CD_FirmwareUpdater_v1.02.02_r1.exe")
print("  Watcher polling every 200ms; copies any new file (esp. .enc / Joro / Talia / firmware patterns) to:")
print(f"  {OUT_DIR}")
print("  Ctrl+C to stop.\n")

copied = set()
INTERESTING_EXT = {'.enc', '.bin', '.fw', '.hex', '.cyacd', '.dll', '.exe', '.ini', '.cfg'}
INTERESTING_NAME = ['joro', 'talia', 'firmware', 'fw_', '.enc', '02cd', '02ce', '009c']

try:
    while True:
        for r in ROOTS:
            current = snapshot(r)
            new_paths = set(current.keys()) - set(baseline[r].keys())
            for path_str in new_paths:
                if path_str in copied:
                    continue
                p = Path(path_str)
                name_lower = p.name.lower()
                ext = p.suffix.lower()
                # Always copy if interesting
                interesting = (ext in INTERESTING_EXT) or any(k in name_lower for k in INTERESTING_NAME)
                # OR copy any file > 50KB created during the watch (potential FW)
                size = current[path_str][0]
                if size > 50_000 and (ext == '' or ext in {'.tmp', '.dat'}):
                    interesting = True
                if interesting:
                    try:
                        # Use a flat unique name in OUT_DIR
                        dst_name = f"{int(time.time()*1000)}_{p.name}"
                        dst = OUT_DIR / dst_name
                        shutil.copy2(p, dst)
                        print(f"  + copied {p}  ({size} bytes)  -> {dst.name}")
                        copied.add(path_str)
                    except (PermissionError, OSError) as e:
                        # File might already be deleted; try to capture by reading
                        try:
                            data = p.read_bytes()
                            (OUT_DIR / f"{int(time.time()*1000)}_{p.name}").write_bytes(data)
                            print(f"  + copied (via read) {p}  ({len(data)} bytes)")
                            copied.add(path_str)
                        except Exception as e2:
                            pass  # gone before we got it
        time.sleep(0.2)
except KeyboardInterrupt:
    print(f"\nStopped. {len(copied)} files copied to {OUT_DIR}")
