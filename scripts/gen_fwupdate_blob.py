"""
Phase 3: extract Razer's COMPLETE DFU transaction (host->device) from
fw_update_u1.pcap into a verbatim 90-byte-frame blob the joro-fwupdate
tool replays. Stock->stock = byte-identical to what Synapse flashed
(and the keyboard already accepted), so NO CRC algorithm needed and it
doubles as the recovery image. CRC reproduction is deferred to Phase 4
(modified images only).
"""
import struct, collections
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
OUT =r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
pk=rdpcap(PCAP)

frames=[]
for p in pk:
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

hist=collections.Counter((f[6],f[7]) for f in frames)
# ordered skeleton (run-length collapsed by class:cmd)
sk=[]; prev=None; n=0
for f in frames:
    k=(f[6],f[7])
    if k==prev: n+=1; continue
    if prev is not None: sk.append((prev,n))
    prev=k; n=1
if prev is not None: sk.append((prev,n))

print(f"wrote {OUT}: {len(frames)} frames x 90 = {len(blob)} bytes")
print("class:cmd histogram: "+', '.join(f'{c:02x}:{d:02x}x{n}' for (c,d),n in sorted(hist.items())))
print("\nordered DFU skeleton (class:cmd xRun):")
print('  '+' -> '.join(f'{c:02x}:{d:02x}'+(f'x{n}' if n>1 else '') for (c,d),n in sk[:40]))
print(f"  ... ({len(sk)} runs total)")
# first/last few for boundary sanity
print("\nfirst 6 frames (class:cmd dsize args[:10]):")
for f in frames[:6]:
    print(f"  {f[6]:02x}:{f[7]:02x} ds={f[5]:2d} {f[8:18].hex()}")
print("last 6 frames:")
for f in frames[-6:]:
    print(f"  {f[6]:02x}:{f[7]:02x} ds={f[5]:2d} {f[8:18].hex()}")
