"""
Phase 1 spec: extract the COMPLETE ordered Protocol30 transaction Synapse
sends to persist a Hypershift remap, from synapse_hypershift_save_u2.pcap.
Output is the implementation spec AND the frame-for-frame validation
oracle for the daemon's class-0x0f VARSTORE commit.

Writes captures/hypershift_save_sequence.txt:
  - full ordered frame list: idx | class:cmd dsize | args(hex, dsize bytes)
  - a run-length-collapsed summary for readability
We keep ONLY host->device SET_REPORT frames (the commands), in order.
"""
import struct
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap'
OUT =r'L:\PROJECTS\razer-joro\captures\hypershift_save_sequence.txt'
pkts=rdpcap(PCAP)

frames=[]
for p in pkts:
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for off in range(20,min(40,len(raw)-8)):
        if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    rz=raw[so+8:so+8+90]
    frames.append((rz[6],rz[7],rz[5],bytes(rz[8:88])))

lines=[]
lines.append(f"# Synapse Hypershift-save transaction — {len(frames)} host->device frames")
lines.append(f"# source: {PCAP}")
lines.append(f"# format: [idx] class:cmd dsize=N  args=<dsize bytes hex>")
lines.append("")
for i,(c,d,ds,a) in enumerate(frames):
    lines.append(f"[{i:4d}] {c:02x}:{d:02x} dsize={ds:2d}  args={a[:max(ds,1)].hex()}")

lines.append("")
lines.append("# ---- run-length-collapsed (class:cmd, identical-args runs) ----")
prev=None; cnt=0; pa=None
for c,d,ds,a in frames:
    key=(c,d,ds,a[:ds])
    if key==prev: cnt+=1; continue
    if prev is not None:
        pc,pd,pds,paa=prev
        lines.append(f"  {pc:02x}:{pd:02x} dsize={pds:2d} args={paa.hex():<28s} x{cnt}")
    prev=key; cnt=1
if prev is not None:
    pc,pd,pds,paa=prev
    lines.append(f"  {pc:02x}:{pd:02x} dsize={pds:2d} args={paa.hex():<28s} x{cnt}")

# phase boundaries: first index of each distinct class:cmd
lines.append("")
lines.append("# ---- first occurrence of each class:cmd (transaction skeleton) ----")
seen=set()
for i,(c,d,ds,a) in enumerate(frames):
    if (c,d) in seen: continue
    seen.add((c,d))
    lines.append(f"  first {c:02x}:{d:02x} @ idx {i}  dsize={ds} args={a[:ds].hex()}")

open(OUT,'w').write('\n'.join(lines))
print(f"wrote {OUT} ({len(frames)} frames)")
print('\n'.join(lines[-40:]))
