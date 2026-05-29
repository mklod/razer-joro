"""
Phase 1: extract the 639 host->device Protocol30 frames of Synapse's
proven Hypershift-save transaction into a verbatim binary blob the daemon
embeds (include_bytes!) and replays. Each frame = exactly 90 bytes
(Razer Protocol30 report, CRC included as captured).

The daemon replays every frame VERBATIM except class=0x02 cmd=0x0d
(set_layer_remap) frames, whose payload it rebuilds from the user's own
Hypershift bindings (faithful replay + binding substitution).
"""
import struct
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap'
OUT =r'L:\PROJECTS\razer-joro\assets\hypershift_replay.bin'
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

blob=b''.join(frames)
import os
os.makedirs(os.path.dirname(OUT),exist_ok=True)
open(OUT,'wb').write(blob)

import collections
hist=collections.Counter((f[6],f[7]) for f in frames)
n0d=sum(1 for f in frames if f[6]==0x02 and f[7]==0x0d)
reads=sum(1 for f in frames if f[7] in (0x8d,0x80,0x81,0x84,0x86,0x8b,0x8e,0x95,0xbf,0xc5,0x82))
print(f"wrote {OUT}: {len(frames)} frames × 90 = {len(blob)} bytes")
print(f"  class=02 cmd=0d (substituted at replay): {n0d}")
print(f"  read/query frames (need response drain):  {reads}")
print(f"  distinct class:cmd: "+', '.join(f'{c:02x}:{d:02x}×{n}' for (c,d),n in sorted(hist.items())))
# sanity: every frame's [5]=dsize must be <=80, [6]/[7] sane
bad=[i for i,f in enumerate(frames) if f[5]>80]
print(f"  frames with dsize>80 (should be 0): {len(bad)}")
