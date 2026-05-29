"""
Dump the exact class=0x0f command sequence (and the 0x02 0x0d layer-remap
frames it interleaves with) from synapse_hypershift_save_u2.pcap, in
capture order, full args + dsize. This is the persist recipe the daemon
must replay after its existing class=0x02 keymap writes.
"""
import struct
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap'
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
    frames.append(raw[so+8:so+8+90])

print(f"{len(frames)} Protocol30 frames\n")
# every class==0x0f frame, with index, and note nearby 02:0d
print("=== all class=0x0f frames (idx: cmd dsize args) ===")
last0d=None
for k,f in enumerate(frames):
    if f[6]==0x0f:
        print(f"  [{k:3d}] cmd=0x{f[7]:02x} dsize={f[5]:2d} "
              f"args={f[8:8+max(f[5],16)].hex()}")
    elif f[6]==0x02 and f[7]==0x0d:
        print(f"  [{k:3d}] (02:0d set_layer_remap dsize={f[5]} "
              f"args={f[8:8+max(f[5],12)].hex()})")

# distinct 0f:cmd templates
print("\n=== distinct class=0x0f (cmd,dsize,args) templates ===")
seen={}
for f in frames:
    if f[6]!=0x0f: continue
    key=(f[7],f[5],f[8:8+f[5]].hex() if f[5] else '')
    seen[key]=seen.get(key,0)+1
for (cmd,ds,a),n in sorted(seen.items()):
    print(f"  cmd=0x{cmd:02x} dsize={ds:2d} args={a or '(none)'}  ×{n}")

# the first occurrence ordering of distinct 0f cmds (the setup->commit shape)
print("\n=== first-seen order of class=0x0f cmds ===")
fo=[]
for f in frames:
    if f[6]==0x0f and f[7] not in [x for x in fo]:
        fo.append(f[7])
print("  "+ ' -> '.join(f"0x{c:02x}" for c in fo))
