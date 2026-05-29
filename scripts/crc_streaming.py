"""
Hypothesis: chunk field a[5:9] is a CUMULATIVE accumulator over all
firmware data streamed so far (per region, in send order), not a
per-chunk CRC. Evidence: identical all-zero data, but CRC=0 for early
chunks and small non-zero for later ones (position-dependent).

Test running accumulators reset per region (tag in a[2]); after
appending each chunk's data, does accumulator == stored a[5:9]?
Try: running sum32, running CRC32 (several polys, init carry), running
xor, running CRC16. Also try accumulator over (data only) vs
(addr+data). Stored LE & BE.
"""
import struct, collections
from scapy.all import rdpcap

pk=rdpcap(r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap')
seq=[]
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
    seq.append({'tag':a[2],'page':a[3],'off':a[4],'crc':bytes(a[5:9]),
                'data':bytes(a[9:9+sz])})
print(f"{len(seq)} chunks, send order")

def le(b): return struct.unpack('<I',b)[0]
def be(b): return struct.unpack('>I',b)[0]

CRC32TAB={}
def crc32_upd(crc,data,poly):
    if poly not in CRC32TAB:
        t=[]
        for i in range(256):
            c=i
            for _ in range(8): c=(c>>1)^poly if c&1 else c>>1
            t.append(c)
        CRC32TAB[poly]=t
    t=CRC32TAB[poly]
    for x in data: crc=(crc>>8)^t[(crc^x)&0xff]
    return crc&0xffffffff

def test(name, upd, init, per_region, endian, stored_xform=lambda c:c):
    acc={} if per_region else {'_':init}
    ef=le if endian=='LE' else be
    okc=0
    for i,c in enumerate(seq):
        k=c['tag'] if per_region else '_'
        if k not in acc: acc[k]=init
        acc[k]=upd(acc[k],c['data'])
        if stored_xform(acc[k]&0xffffffff)==ef(c['crc']): okc+=1
    return okc

N=len(seq)
print("acc model                                  matches/total")
cands=[
 ("sum32 per-region",            lambda a,d:(a+sum(d))&0xffffffff, 0, True),
 ("sum32 global",                lambda a,d:(a+sum(d))&0xffffffff, 0, False),
 ("xor-bytes32 per-region",      lambda a,d:a^(sum(d)&0xffffffff), 0, True),
 ("crc32/EDB88320 per-region",   lambda a,d:crc32_upd(a,d,0xEDB88320), 0, True),
 ("crc32/EDB88320 init~0 perreg",lambda a,d:crc32_upd(a,d,0xEDB88320), 0xffffffff, True),
 ("crc32/EDB88320 global",       lambda a,d:crc32_upd(a,d,0xEDB88320), 0, False),
 ("crc32/82F63B78(C) per-region",lambda a,d:crc32_upd(a,d,0x82F63B78), 0, True),
 ("count-bytes per-region",      lambda a,d:a+len(d), 0, True),
]
best=None
for nm,upd,init,pr in cands:
    for en in ('LE','BE'):
        for xf,xn in ((lambda c:c,''),(lambda c:c^0xffffffff,'^FFFFFFFF')):
            m=test(nm,upd,init,pr,en,xf)
            if best is None or m>best[0]: best=(m,f"{nm} {en}{xn}")
            if m>N*0.3:
                print(f"  {nm:38s} {en}{xn:11s} {m}/{N}")
print(f"\nbest: {best[1]} = {best[0]}/{N}")

# also: is a[5:9] maybe the running count of NON-zero bytes, or the
# address of next write? quick look at first 12 non-trivial chunks
print("\nfirst 12 chunks w/ nonzero data (tag,page,off,crc, data[:6]):")
shown=0
for c in seq:
    if any(c['data']):
        print(f"  tag={c['tag']:02x} pg={c['page']:02x} off={c['off']:02x} "
              f"crc={c['crc'].hex()} data={c['data'][:6].hex()}")
        shown+=1
        if shown>=12: break
