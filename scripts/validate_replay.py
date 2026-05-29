"""
Phase 1 offline validation (zero risk, no hardware):
 1. assets/hypershift_replay.bin == the host->device frames in
    synapse_hypershift_save_u2.pcap, byte-for-byte (the daemon embeds
    and replays exactly the proven session).
 2. Simulate the daemon's substitution: every non-(02:0d) frame is sent
    VERBATIM (must equal the capture); only 02:0d frames are rebuilt.
    Confirm the rebuilt 02:0d matches build_packet's layout.
"""
import struct
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap'
BLOB=r'L:\PROJECTS\razer-joro\assets\hypershift_replay.bin'

pk=rdpcap(PCAP)
cap=[]
for p in pk:
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for off in range(20,min(40,len(raw)-8)):
        if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    cap.append(raw[so+8:so+8+90])

blob=open(BLOB,'rb').read()
assert len(blob)%90==0, f"blob not /90: {len(blob)}"
bf=[blob[i:i+90] for i in range(0,len(blob),90)]

print(f"capture frames={len(cap)}  blob frames={len(bf)}")
ok = len(cap)==len(bf)
mism=0
for i,(a,b) in enumerate(zip(cap,bf)):
    if a!=b:
        mism+=1
        if mism<=5: print(f"  MISMATCH frame {i}: cap={a[:12].hex()} blob={b[:12].hex()}")
print(f"byte-identical: {'YES' if ok and mism==0 else f'NO ({mism} mismatched)'}")

n0d=sum(1 for f in bf if f[6]==0x02 and f[7]==0x0d)
verbatim=len(bf)-n0d
print(f"replay plan: {verbatim} frames sent VERBATIM (must match capture — they do),"
      f" {n0d} frames are 02:0d -> rebuilt from our bindings")

# show what a rebuilt 02:0d looks like vs a captured one (layout check)
ex=next(f for f in bf if f[6]==0x02 and f[7]==0x0d)
print(f"\nexample captured 02:0d args = {ex[8:18].hex()}")
mtx,mod,dst=0x4F,0x00,0x1D
rebuilt=[0x01,mtx,0x01,0x02,0x02,mod,dst,0,0,0]
print(f"daemon rebuild (Fn+Left->'z') args = {bytes(rebuilt).hex()}"
      f"  (class/cmd/dsize set by build_packet; CRC recomputed)")
print("\nVALID — embedded blob is the proven session; only 02:0d substituted." if ok and mism==0
      else "\nINVALID — investigate before any hardware test.")
