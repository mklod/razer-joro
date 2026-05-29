"""
Diff capture #1 (dongle_setup_full_u1.pcap, Synapse boot + first-time pair)
vs capture #2 (dongle_pair2_u1.pcap, unpair + re-pair).

Goal: isolate device/session-specific bytes from the constant pair
protocol. The 0xda in 00:41 args=01 02 da (frame 63 of capture #1) is
the prime suspect for a per-pair session token.

Strategy:
 1. Extract all Protocol30 SET_REPORT frames from each pcap.
 2. Find each capture's PAIR-TRIGGER (0b:03 args=00 04 00) AND the
    UNPAIR sequence (likely 0b:03 with a different arg, or class 0x05/0x06).
 3. Align both pair windows by anchoring on the 0b:03 trigger.
 4. Per-frame side-by-side diff for the ±15 frames around the trigger.
 5. Highlight any byte position that differs between captures — those
    are device/session-specific.
"""
import struct, collections
from scapy.all import rdpcap

def extract(pcap_path):
    frames = []
    for p in rdpcap(pcap_path):
        raw = bytes(p)
        if len(raw) < 29: continue
        hlen = struct.unpack('<H', raw[0:2])[0]
        if hlen + 8 > len(raw): continue
        so = None
        for o in range(max(0, hlen-4), min(hlen+10, len(raw)-8)):
            if raw[o]==0x21 and raw[o+1]==0x09:
                if struct.unpack('<H', raw[o+6:o+8])[0] == 90:
                    so = o; break
        if so is None: continue
        rz = raw[so+8:so+8+90]
        if len(rz) < 90: continue
        frames.append((rz[6], rz[7], rz[5], bytes(rz[8:88])))
    return frames

C1 = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
C2 = r'L:\PROJECTS\razer-joro\captures\dongle_pair2_u1.pcap'

f1 = extract(C1)
f2 = extract(C2)
print(f"capture #1 ({C1}): {len(f1)} Protocol30 SET_REPORT frames")
print(f"capture #2 ({C2}): {len(f2)} Protocol30 SET_REPORT frames")

# class:cmd histogram diff — what classes appear MORE in capture #2 vs #1?
h1 = collections.Counter((c,d) for c,d,ds,a in f1)
h2 = collections.Counter((c,d) for c,d,ds,a in f2)
print("\n=== class:cmd delta (c2 - c1, signed) ===")
all_keys = set(h1.keys()) | set(h2.keys())
for k in sorted(all_keys):
    delta = h2.get(k, 0) - h1.get(k, 0)
    if delta != 0:
        print(f"  {k[0]:02x}:{k[1]:02x}  c1={h1.get(k,0):3d} c2={h2.get(k,0):3d}  delta={delta:+d}")

# Find every PAIR-TRIGGER candidate (0b:03 args=00 04 00 anywhere)
# AND every variant of 0b:03 (could be unpair too)
print("\n=== 0b:03 occurrences in each capture ===")
for tag, f in (('c1', f1), ('c2', f2)):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x0b and d == 0x03:
            print(f"  [{tag}][{i:4d}] 0b:03 ds={ds} args={a[:ds].hex()}")

# Find UNPAIR candidates — new ops we expect only in capture #2 (which
# does unpair-then-repair vs c1 which only paired).
print("\n=== ops present in c2 but NOT c1 (likely UNPAIR-specific) ===")
only_c2 = {k for k in h2 if k not in h1}
for k in sorted(only_c2):
    # find first occurrence in c2
    for i, (c, d, ds, a) in enumerate(f2):
        if (c, d) == k:
            print(f"  c2[{i:4d}] {c:02x}:{d:02x} ds={ds} args={a[:max(ds,1)].hex()}")
            break

# Find 00:41 occurrences in both — the BOND WRITE candidate
print("\n=== 00:41 (bond write) occurrences ===")
for tag, f in (('c1', f1), ('c2', f2)):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x00 and d == 0x41:
            print(f"  [{tag}][{i:4d}] 00:41 ds={ds} args={a[:max(ds,8)].hex()}")

# Side-by-side pair-window diff: anchor each capture on its 0b:03 trigger
def find_pair_trigger(f):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x0b and d == 0x03 and ds == 3 and a[0] == 0x00 and a[1] == 0x04:
            return i
    return None

t1 = find_pair_trigger(f1)
t2 = find_pair_trigger(f2)
print(f"\n=== Pair-trigger anchor: c1@{t1}  c2@{t2} ===")

if t1 is not None and t2 is not None:
    W = 20  # window radius
    print(f"\nFrames around pair trigger (±{W}):  format: [c1_idx | c2_idx] op ds args")
    for off in range(-W, W+1):
        i1, i2 = t1+off, t2+off
        v1 = f1[i1] if 0 <= i1 < len(f1) else None
        v2 = f2[i2] if 0 <= i2 < len(f2) else None
        def fmt(v, idx):
            if v is None: return f"[----]"
            c,d,ds,a = v
            ah = a[:max(ds,1)].hex()
            return f"[{idx:4d}] {c:02x}:{d:02x} ds={ds:2d} {ah:<32s}"
        mark = ''
        if v1 is not None and v2 is not None:
            # same op?
            same_op = (v1[0],v1[1],v1[2]) == (v2[0],v2[1],v2[2])
            same_args = v1[3][:v1[2]] == v2[3][:v2[2]]
            if not same_op: mark = ' <-- OP MISMATCH'
            elif not same_args: mark = ' <-- ARGS DIFFER'
        print(f"  off={off:+3d}  {fmt(v1,i1)}    {fmt(v2,i2)}{mark}")

# Final: any byte that differs between the two pair windows — flag as
# device-specific. Specifically look at 00:41 args.
print("\n=== 00:41 args byte-by-byte diff ===")
b1 = [a for c,d,ds,a in f1 if c==0x00 and d==0x41]
b2 = [a for c,d,ds,a in f2 if c==0x00 and d==0x41]
print(f"  c1 has {len(b1)} 00:41 frames; c2 has {len(b2)}")
for i, (a1, a2) in enumerate(zip(b1, b2)):
    print(f"  pair #{i}:")
    print(f"    c1 args: {a1[:16].hex()}")
    print(f"    c2 args: {a2[:16].hex()}")
    diffs = [(j, a1[j], a2[j]) for j in range(16) if a1[j] != a2[j]]
    print(f"    diffs at {diffs}")
