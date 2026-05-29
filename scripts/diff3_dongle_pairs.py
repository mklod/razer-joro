"""
3-way diff:
  c1 = dongle_setup_full_u1.pcap   (dongle #1, first-time Joro pair via Synapse)
  c2 = dongle_pair2_u1.pcap        (dongle #1, unpair + re-pair via Synapse)
  c3 = dongle3_u1.pcap             (Joro unpair from #1, dongle swap to #2,
                                    standalone utility mouse-pair attempt on #2,
                                    Synapse Joro pair on #2 — ghost bond present)

Cross-validates:
 - 0xda Joro model-id across DIFFERENT physical dongles
 - mouse-pair protocol (utility) — never captured before
 - dongle-swap traffic and ghost-bond cleanup
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
C3 = r'L:\PROJECTS\razer-joro\captures\dongle3_u1.pcap'

f1 = extract(C1)
f2 = extract(C2)
f3 = extract(C3)
print(f"c1: {len(f1)} frames")
print(f"c2: {len(f2)} frames")
print(f"c3: {len(f3)} frames")

# === All 00:41 (bond write) frames across all captures ===
print("\n=== ALL 00:41 (BOND WRITE) frames — testing 0xda cross-dongle ===")
for tag, f in (('c1', f1), ('c2', f2), ('c3', f3)):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x00 and d == 0x41:
            print(f"  [{tag}][{i:5d}] 00:41 ds={ds} args={a[:max(ds,8)].hex()}")

# === All 00:42 (UNPAIR) frames ===
print("\n=== ALL 00:42 (UNPAIR) frames ===")
for tag, f in (('c1', f1), ('c2', f2), ('c3', f3)):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x00 and d == 0x42:
            print(f"  [{tag}][{i:5d}] 00:42 ds={ds} args={a[:max(ds,8)].hex()}")

# === All 0b:03 (DISCOVERY TRIGGER) frames ===
print("\n=== ALL 0b:03 (DISCOVERY) frames ===")
for tag, f in (('c1', f1), ('c2', f2), ('c3', f3)):
    for i, (c, d, ds, a) in enumerate(f):
        if c == 0x0b and d == 0x03:
            print(f"  [{tag}][{i:5d}] 0b:03 ds={ds} args={a[:max(ds,8)].hex()}")

# === Class:cmd that appear in c3 but NOT in c1+c2 — the utility's mouse-pair? ===
known = set((c,d) for c,d,ds,a in f1) | set((c,d) for c,d,ds,a in f2)
c3_only = set((c,d) for c,d,ds,a in f3) - known
print(f"\n=== class:cmd in c3 only (vs c1+c2): {len(c3_only)} ===")
for k in sorted(c3_only):
    for i, (c, d, ds, a) in enumerate(f3):
        if (c, d) == k:
            print(f"  c3[{i:5d}] {c:02x}:{d:02x} ds={ds} args={a[:max(ds,8)].hex()}")
            break

# === Joro PAIR context in c3 — find the 00:41 and dump ±15 frames ===
joro_pair_in_c3 = [i for i,(c,d,ds,a) in enumerate(f3) if c==0x00 and d==0x41]
print(f"\n=== c3 Joro-pair context (frames around each 00:41) ===")
for anchor in joro_pair_in_c3:
    print(f"  --- 00:41 at c3[{anchor}] (±10) ---")
    for i in range(max(0,anchor-10), min(len(f3),anchor+10)):
        c,d,ds,a = f3[i]
        star = '  *' if i == anchor else '   '
        print(f"  {star}[{i:5d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,8)].hex()}")

# === Mouse pair candidates — look for 0b:03 with a different slot or
#     class 0x0b frames that aren't the Joro-slot-4 one ===
print("\n=== c3 dongle-management commands (class 0x04/0x05/0x06/0x0b) — first 30 + around any 0b ===")
mgmt = [i for i,(c,_,_,_) in enumerate(f3) if c in (0x04,0x05,0x06,0x0b)]
print(f"  total: {len(mgmt)}")
zerobash = [i for i,(c,d,_,_) in enumerate(f3) if c == 0x0b]
print(f"  class 0x0b count: {len(zerobash)}")
SHOW = set(mgmt[:20])
for i in zerobash:
    for j in range(max(0,i-5), min(len(f3),i+8)): SHOW.add(j)
for i in sorted(SHOW):
    c,d,ds,a = f3[i]
    flag = ''
    if c == 0x0b: flag = '  <-- 0b:'
    print(f"  [{i:5d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,8)].hex()}{flag}")
