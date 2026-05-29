"""
Phase 4 CRC, smarter angles (the catalog brute failed; CRC covers
header/addr; 142 identical-data chunks -> 9 distinct CRCs):

 1. Correlate the big identical-data group: which header bytes vary,
    and how do they map onto the 9 distinct CRC values? Reverse-maps
    the CRC INPUT domain.
 2. Simple non-CRC checksums (sum8/16/32, xor32, Fletcher, Adler-32)
    over several input ranges, stored LE/BE — vendors often use these.
 3. Scan region 03 for a 256-entry CRC lookup table (the giveaway:
    table[1] == reflected poly e.g. 0x77073096 for 0xEDB88320, or
    0x04C11DB7-derived forward table) so we can read the exact poly.
"""
import struct, collections
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
pk=rdpcap(PCAP)
ch=[]
for p in pk:
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for o in range(20,min(40,len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09: so=o;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    rz=raw[so+8:so+8+90]
    if rz[6]!=0x10 or rz[7]!=0x02: continue
    a=rz[8:88]; sz=struct.unpack('<H',a[0:2])[0]
    ch.append({'hdr':bytes(a[0:5]),'crc':bytes(a[5:9]),'data':bytes(a[9:9+sz]),'sz':sz})
print(f"{len(ch)} chunks")

# 1. identical-data correlation
grp=collections.defaultdict(list)
for c in ch: grp[c['data']].append(c)
big=max(grp.values(),key=len)
print(f"\nbiggest identical-data group: n={len(big)} (data {len(big[0]['data'])}B, "
      f"all-zero={big[0]['data']==bytes(len(big[0]['data']))})")
m=collections.defaultdict(list)
for c in big: m[c['crc']].append(c['hdr'])
print(f"  {len(m)} distinct CRC across the group:")
for crc,hdrs in sorted(m.items())[:12]:
    hs=sorted(set(h.hex() for h in hdrs))
    print(f"   crc={crc.hex()}  <- {len(hdrs)} chunks, hdrs(0:5)={hs[:6]}")

# 2. simple checksums
def le(b): return struct.unpack('<I',b)[0]
def be(b): return struct.unpack('>I',b)[0]
def fletcher32(d):
    s1=s2=0
    if len(d)%2: d=d+b'\x00'
    for i in range(0,len(d),2):
        s1=(s1+(d[i]|(d[i+1]<<8)))%65535; s2=(s2+s1)%65535
    return (s2<<16)|s1
def adler32(d):
    a=1;b=0
    for x in d: a=(a+x)%65521; b=(b+a)%65521
    return (b<<16)|a
RNG={'data':lambda c:c['data'],'hdr+data':lambda c:c['hdr']+c['data'],
     'hdr2_4+data':lambda c:c['hdr'][2:5]+c['data'],'data+hdr':lambda c:c['data']+c['hdr']}
samp=ch[:80]
print("\nsimple-checksum sweep (match 80 chunks):")
hit=False
for rn,rf in RNG.items():
    for en,ef in (('LE',le),('BE',be)):
        tests={
         'sum32':   lambda d:sum(d)&0xffffffff,
         'xor32':   lambda d:__import__('functools').reduce(lambda a,b:a^b,
                      [struct.unpack('<I',d[i:i+4].ljust(4,b"\0"))[0] for i in range(0,len(d),4)],0),
         'sum16':   lambda d:sum(d)&0xffff,
         'fletch32':fletcher32,'adler32':adler32,
        }
        for tn,tf in tests.items():
            if all((tf(rf(c))&0xffffffff)==ef(c['crc']) for c in samp):
                print(f"  *** MATCH {tn} range={rn} {en}"); hit=True
if not hit: print("  (no simple-checksum match)")

# 3. firmware CRC table scan
for tag in ('03_at_0x0000','02_at_0x7000','04_at_0x0000'):
    d=open(rf'L:\PROJECTS\razer-joro\captures\joro_region_{tag}.bin','rb').read()
    found=[]
    for off in range(0,len(d)-1024,4):
        w0=struct.unpack('<I',d[off:off+4])[0]
        w1=struct.unpack('<I',d[off+4:off+8])[0]
        if w0==0x00000000 and w1 in (0x77073096,0x04C11DB7,0x1DB71064,0xEDB88320):
            # verify a few more entries look table-like (monotone-ish hi nibble churn)
            found.append((off,w1))
    if found:
        for off,w1 in found[:4]:
            print(f"\nregion {tag}: CRC table @0x{off:05x} entry[1]=0x{w1:08x} "
                  f"(poly {'0xEDB88320 refl' if w1==0x77073096 else hex(w1)})")
    else:
        print(f"\nregion {tag}: no standard CRC32 table signature")
