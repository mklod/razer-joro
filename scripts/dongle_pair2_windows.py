"""
In capture #2 (dongle_pair2_u1.pcap):
  - 00:42 args=02 da at c2[910]  ← UNPAIR
  - 00:41 args=01 02 da at c2[1644]  ← RE-PAIR bond write
The pair-trigger 0b:03 is ABSENT. Find what the actual re-pair-trigger
is in c2. Strategy: find a frame near c2[1644] that has class 0x0b OR
something we don't see in steady-state.

Also, isolate every frame whose class is dongle-management (0x04,0x05,
0x06,0x0b) in c2 — those are the candidates for the missing trigger.
"""
import struct, collections
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_pair2_u1.pcap'
frames = []
for p in rdpcap(PCAP):
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

print(f"c2 total: {len(frames)} frames")

DONGLE_MGMT = {0x04, 0x05, 0x06, 0x0b}
def fmt(i):
    c, d, ds, a = frames[i]
    return f"[{i:5d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,8)].hex()}"

# All dongle-management frames
print("\n=== ALL dongle-management (class 0x04/0x05/0x06/0x0b) frames in c2 ===")
mgmt_idx = [i for i,(c,_,_,_) in enumerate(frames) if c in DONGLE_MGMT]
print(f"  count: {len(mgmt_idx)}")
# Show first 8 and any near the unpair/repair anchors
SHOW = set(mgmt_idx[:8]) | set(mgmt_idx[-8:])
# also show all within ±30 of unpair (910) and repair (1644)
for i in mgmt_idx:
    if abs(i-910) <= 30 or abs(i-1644) <= 30: SHOW.add(i)
for i in sorted(SHOW):
    print(f"  {fmt(i)}")

# Window around UNPAIR (c2[910])
print(f"\n=== UNPAIR window: c2[{910-15}..{910+25}] ===")
for i in range(max(0,910-15), min(len(frames),910+25)):
    star = '  *' if i == 910 else '   '
    print(f"{star}{fmt(i)}")

# Window around RE-PAIR BOND WRITE (c2[1644])
print(f"\n=== RE-PAIR BOND window: c2[{1644-30}..{1644+15}] ===")
for i in range(max(0,1644-30), min(len(frames),1644+15)):
    star = '  *' if i == 1644 else '   '
    print(f"{star}{fmt(i)}")

# What's the SMALLEST set of *new* commands (vs steady-state) that
# appears in the pair-region (say 1600..1670)? Compare to a steady-state
# region (e.g. 200..270).
print("\n=== ops in re-pair region 1600..1670 NOT in steady-state 200..270 ===")
steady = collections.Counter((c,d) for c,d,_,_ in frames[200:270])
pair = collections.Counter((c,d) for c,d,_,_ in frames[1600:1670])
for k in sorted(set(pair) - set(steady)):
    print(f"  {k[0]:02x}:{k[1]:02x}  x{pair[k]}")

print("\n=== ops in unpair region 880..940 NOT in steady-state 200..270 ===")
unpair = collections.Counter((c,d) for c,d,_,_ in frames[880:940])
for k in sorted(set(unpair) - set(steady)):
    print(f"  {k[0]:02x}:{k[1]:02x}  x{unpair[k]}")
