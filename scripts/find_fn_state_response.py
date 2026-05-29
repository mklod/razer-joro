"""
Analyze a frida_find_dongle_send_ioctl.py trace file to find which
Protocol30 query response data changes during a known Fn-held interval.

Strategy: bucket every (class, cmd) packet's response data by trans_id;
report any (class, cmd) whose response bytes vary across packets. The Fn
state poll will be one whose response args[N] flip 0/1 over time.
"""
import re, sys
from collections import defaultdict

path = sys.argv[1] if len(sys.argv) > 1 else "L:/PROJECTS/razer-joro/captures/frida_fn_trace.log"

with open(path, encoding='latin-1') as f:
    data = f.read()

# Parse PROTOCOL30 entries
entries = []
for m in re.finditer(
    r'\[\s*(\S+)\]\s+\[(\d+)\]\s+\*\*\* PROTOCOL30 \*\*\* ioctl=(\S+) ret=(\S+) inLen=(\d+) outLen=(\d+).*?\n\s+path:.*?\n\s+hex:\s+([\da-f ]+)',
    data, re.S):
    ts = float(m.group(1))
    pid = m.group(2)
    ioctl = m.group(3)
    ret = m.group(4)
    out_len = int(m.group(6))
    bytes_ = m.group(7).strip().split()
    if len(bytes_) < 12:
        continue
    # bytes_[0] = report id; bytes_[1] = status; bytes_[2] = trans_id;
    # bytes_[6] = dsize; bytes_[7] = class; bytes_[8] = cmd
    status = bytes_[1]
    trans = bytes_[2]
    dsize = bytes_[6]
    cls = bytes_[7]
    cmd = bytes_[8]
    args = bytes_[9:9 + max(int(dsize, 16), 8)]
    entries.append({
        'ts': ts, 'pid': pid, 'ioctl': ioctl, 'ret': ret, 'out_len': out_len,
        'status': status, 'trans': trans, 'dsize': dsize, 'class': cls, 'cmd': cmd,
        'args': ' '.join(args), 'all_bytes': ' '.join(bytes_[1:24]),
    })

print(f"Parsed {len(entries)} PROTOCOL30 entries\n")

# Group by (class, cmd, ioctl). For GETs (0xb0192) the args field carries
# the device's response. We want (class, cmd) where args VARY.
buckets = defaultdict(list)  # (class, cmd, ioctl) -> [(ts, args)]
for e in entries:
    buckets[(e['class'], e['cmd'], e['ioctl'])].append((e['ts'], e['args'], e['status']))

print("(class, cmd, ioctl) breakdown:")
for k, v in sorted(buckets.items(), key=lambda x: -len(x[1])):
    cls, cmd, ioctl = k
    distinct_args = set(args for _ts, args, _st in v)
    marker = " *** VARIES ***" if len(distinct_args) > 1 else ""
    print(f"  class=0x{cls} cmd=0x{cmd} ioctl={ioctl}: {len(v)} packets, {len(distinct_args)} distinct args{marker}")

print("\nVarying responses (potential Fn-state encoders):")
for k, v in sorted(buckets.items()):
    cls, cmd, ioctl = k
    if ioctl != "0xb0192":  # only GET responses
        continue
    distinct_args = sorted(set((args, status) for _ts, args, status in v))
    if len(distinct_args) > 1:
        print(f"\n  class=0x{cls} cmd=0x{cmd}: {len(distinct_args)} distinct (args, status):")
        # Show timeline
        for ts, args, status in sorted(v):
            print(f"    ts={ts:7.2f} status={status} args={args}")
