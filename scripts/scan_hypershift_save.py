"""
Scan the Synapse "save Hypershift" captures for Razer Protocol30 frames.
We expect a class==0xf0 record-write sequence (the firmware persist path
0x0bb88->0x0c0a4 we just RE'd) plus whatever sets the enable mode.

Razer Protocol30 over HID SET_REPORT(Feature): setup 21 09, wLength==90,
then 90-byte report: [0]=status [1]=txid [2:4]=remaining [4]=proto
[5]=dsize [6]=CLASS [7]=CMD [8:88]=args [88]=crc.
"""
import struct, collections
from scapy.all import rdpcap

for PCAP in (r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u1.pcap',
             r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap'):
    print(f"\n{'='*72}\n{PCAP.split(chr(92))[-1]}")
    pkts = rdpcap(PCAP)
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
        frames.append(rz)
    cls=collections.Counter(f[6] for f in frames)
    print(f"  {len(frames)} Protocol30 frames; class histogram: "
          + ', '.join(f'0x{c:02x}×{n}' for c,n in cls.most_common()))
    # show class==0xf0 frames in detail, plus class==0x03/0x02 (keymap) and
    # anything carrying a 0xf0 / mode-set look
    for tag,sel in (('CLASS 0xf0 (persist/record)', lambda f:f[6]==0xf0),
                    ('CLASS 0x03 (keymap/profile)', lambda f:f[6]==0x03),
                    ('CLASS 0x02 (keymap)',          lambda f:f[6]==0x02)):
        hits=[f for f in frames if sel(f)]
        if not hits: continue
        print(f"\n  -- {tag}: {len(hits)} frames (first 12) --")
        for f in hits[:12]:
            print(f"    class=0x{f[6]:02x} cmd=0x{f[7]:02x} dsize={f[5]:3d} "
                  f"args[0:16]={f[8:24].hex()}")
    # also: the full ordered command stream (class,cmd) to see sequence
    print("\n  -- ordered (class,cmd) stream (deduped runs) --")
    seq=[]
    for f in frames:
        k=(f[6],f[7])
        if not seq or seq[-1][0]!=k: seq.append([k,1])
        else: seq[-1][1]+=1
    line=' '.join(f"{c:02x}:{d:02x}" + (f"x{n}" if n>1 else '') for (c,d),n in seq)
    print("   "+line[:1500])
