"""
Phase 3 offline validation (zero risk, no hardware):
assets/fwupdate_stock_replay.bin must be byte-for-byte identical to the
host->device frames in fw_update_u1.pcap. If so, `fw-flash-stock --commit`
sends EXACTLY what Razer's updater sent in a session the keyboard
already accepted -> safest possible flash (true stock round-trip).
"""
import struct
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
BLOB=r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'

cap=[]
for p in rdpcap(PCAP):
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for off in range(20,min(40,len(raw)-8)):
        if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    cap.append(raw[so+8:so+8+90])

blob=open(BLOB,'rb').read()
assert len(blob)%90==0
bf=[blob[i:i+90] for i in range(0,len(blob),90)]
print(f"capture host->device frames={len(cap)}  blob frames={len(bf)}")
mism=sum(1 for a,b in zip(cap,bf) if a!=b)
same=len(cap)==len(bf) and mism==0
print(f"byte-identical: {'YES' if same else f'NO (len {len(cap)} vs {len(bf)}, {mism} mismatch)'}")

# integrity: every 10:02 chunk's captured 4-byte CRC is carried verbatim
chunks=[f for f in bf if f[6]==0x10 and f[7]==0x02]
init=[f for f in bf if f[6]==0x10 and f[7]==0x01]
print(f"chunks(10:02)={len(chunks)}  init(10:01)={len(init)}  "
      f"commit(10:05)={sum(1 for f in bf if f[6]==0x10 and f[7]==0x05)}  "
      f"reboot(00:0b)={sum(1 for f in bf if f[6]==0x00 and f[7]==0x0b)}")
if init:
    print(f"init args = {init[0][8:16].hex()}  (base+size+crc — replayed verbatim)")
# total firmware payload (sanity vs the ~158KB plaintext image)
fw=sum(f[8] | (f[9]<<8) for f in chunks)  # size field per chunk hdr
print(f"sum of chunk size fields = {fw} bytes (~{fw/1024:.1f} KB firmware)")
print("\nVALID — real flash == Synapse's accepted session, byte for byte."
      if same else "\nINVALID — do NOT flash.")
