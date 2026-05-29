"""
Save the full ordered Synapse dongle-setup + Joro-pair transaction as a
reference doc, like hypershift_save_sequence. With full per-frame args
we can pin the exact pair-trigger + post-pair init commands and design
the Synapse-free replay tool.
"""
import struct
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
OUT  = r'L:\PROJECTS\razer-joro\captures\dongle_setup_sequence.txt'
pk = rdpcap(PCAP)

frames = []
for p in pk:
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen < 27 or hlen + 8 > len(raw): continue
    so = None
    for o in range(hlen-2, min(hlen+6, len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09: so=o; break
    if so is None:
        for o in range(20, min(40, len(raw)-8)):
            if raw[o]==0x21 and raw[o+1]==0x09: so=o; break
    if so is None: continue
    if struct.unpack('<H', raw[so+6:so+8])[0] != 90: continue
    rz = raw[so+8:so+8+90]
    frames.append((rz[6], rz[7], rz[5], bytes(rz[8:88])))

lines = [
    f"# Synapse dongle-setup + Joro-pair (PID 0x009C) — {len(frames)} frames",
    f"# source: {PCAP}",
    "# format: [idx] class:cmd dsize=N  args=<full dsize bytes hex>",
    "",
]
for i, (c, d, ds, a) in enumerate(frames):
    lines.append(f"[{i:4d}] {c:02x}:{d:02x} dsize={ds:2d}  args={a[:max(ds,1)].hex()}")

# also: run-length collapsed and first-seen ordering
lines.append("\n# ---- run-length-collapsed (same class:cmd + identical-args runs) ----")
prev=None; cnt=0
for c,d,ds,a in frames:
    key=(c,d,ds,a[:ds])
    if key==prev: cnt+=1; continue
    if prev is not None:
        pc,pd,pds,paa=prev
        lines.append(f"  {pc:02x}:{pd:02x} ds={pds:2d} args={paa.hex():<28s} x{cnt}")
    prev=key; cnt=1
if prev is not None:
    pc,pd,pds,paa=prev
    lines.append(f"  {pc:02x}:{pd:02x} ds={pds:2d} args={paa.hex():<28s} x{cnt}")

lines.append("\n# ---- first occurrence of each class:cmd (transaction skeleton) ----")
seen=set()
for i,(c,d,ds,a) in enumerate(frames):
    if (c,d) in seen: continue
    seen.add((c,d))
    lines.append(f"  first {c:02x}:{d:02x} @ idx {i:4d}  dsize={ds} args={a[:max(ds,1)].hex()}")

open(OUT, 'w').write('\n'.join(lines))
print(f"wrote {OUT}  ({len(frames)} frames)")
print('\n'.join(lines[-30:]))
