#!/usr/bin/env python3
"""Parse a Procmon .pml file and print only interesting device I/O events.

Filters for events where:
  - operation is DeviceIoControl OR related device I/O
  - path contains 'rzcontrol' or 'HID#' or 'BTHLEDEVICE' or 'BTH'
  - OR process name contains 'razer', 'Razer', 'Rz', 'rz', 'Game'

Usage:
  python parse_procmon.py <file.pml>
"""
import sys
from procmon_parser import ProcmonLogsReader

if len(sys.argv) < 2:
    print("usage: parse_procmon.py <file.pml>", file=sys.stderr)
    sys.exit(1)

path_keywords = ("rzcontrol", "HID#", "BTHLEDEVICE", "BTHLEDevice", "_02ce", "_02cd", "\\RzDev")
proc_keywords = ("razer", "Razer", "Rz", "Chroma", "Game", "Synapse")

total = 0
matched = 0
ops_seen = {}

with open(sys.argv[1], "rb") as f:
    reader = ProcmonLogsReader(f)
    for record in reader:
        total += 1
        op = str(record.operation)
        ops_seen[op] = ops_seen.get(op, 0) + 1

        # We want device I/O only: DeviceIoControl, WriteFile, CreateFile.
        # Not registry, not network, not typical file stuff.
        if op not in ("DeviceIoControl", "CreateFile", "WriteFile", "ReadFile"):
            continue

        p = record.process
        proc_name = p.process_name if p else ""
        path = record.path or ""

        # Is it a Razer-ish process?
        is_razer_proc = any(k in proc_name for k in proc_keywords)
        # Is it a device-ish path we care about?
        is_dev_path = any(k in path for k in path_keywords)

        # Match if either: Razer process doing any device I/O
        # OR non-razer process touching a Joro path
        if not (is_razer_proc or is_dev_path):
            continue

        matched += 1
        details = record.details or ""
        # Truncate details to 200 chars to keep output manageable
        if len(details) > 200:
            details = details[:200] + "..."
        ts = record.date_filetime
        print(f"[{matched:5}] pid={p.pid if p else '-':>6} {proc_name:25} {op:18} {path}")
        if details and op == "DeviceIoControl":
            print(f"         details: {details}")

print(f"\n=== stats ===", file=sys.stderr)
print(f"total events: {total}", file=sys.stderr)
print(f"matched:      {matched}", file=sys.stderr)
print(f"\noperation counts:", file=sys.stderr)
for op, n in sorted(ops_seen.items(), key=lambda x: -x[1])[:20]:
    print(f"  {n:>8} {op}", file=sys.stderr)
