"""
Phase 3 crux: reproduce Razer's per-chunk CRC so we can flash a MODIFIED
firmware image. We have 2470 (chunk-header+data, 4-byte-CRC) pairs in
fw_update_u1.pcap.

D=9 chunk layout (args, 0-based within the 80-byte arg block):
  [0:2] size LE  [2] tag  [3] page  [4] off  [5:9] CRC(4B)  [9:9+size] data

Strategy:
  1. Discriminator: are two chunks with IDENTICAL data but different
     address given the SAME crc? -> CRC is data-only. Different -> CRC
     covers header/address too.
  2. Brute a parametric CRC-32 over the standard catalog (poly, init,
     refin, refout, xorout) x several input ranges x stored-endianness.
     Correct params match ALL 2470. Also try CRC-16 (4-byte field could
     be crc16 + 2 pad).
"""
import struct, collections
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
pk=rdpcap(PCAP)
chunks=[]
for p in pk:
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for off in range(20,min(40,len(raw)-8)):
        if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    rz=raw[so+8:so+8+90]
    if rz[6]!=0x10 or rz[7]!=0x02: continue
    a=rz[8:88]
    size=struct.unpack('<H',a[0:2])[0]
    crc=bytes(a[5:9])
    chunks.append({'hdr':bytes(a[0:5]),'crc':crc,'data':bytes(a[9:9+size]),
                   'all':bytes(a[0:5])+bytes(a[9:9+size]),'size':size})
print(f"{len(chunks)} cmd=0x02 chunks")

# (1) data-only vs address-covered discriminator
bydata=collections.defaultdict(set)
for c in chunks: bydata[c['data']].add(c['crc'])
dups=[(d,cs) for d,cs in bydata.items() if len(cs)>1 or sum(1 for c in chunks if c['data']==d)>1]
ident=[d for d,cs in bydata.items() if sum(1 for c in chunks if c['data']==d)>1]
if ident:
    d0=ident[0]
    cs=set(c['crc'] for c in chunks if c['data']==d0)
    print(f"  identical-data group (n={sum(1 for c in chunks if c['data']==d0)}): "
          f"{len(cs)} distinct CRC -> {'DATA-ONLY' if len(cs)==1 else 'CRC COVERS HEADER/ADDR'}")
else:
    print("  (no identical-data chunks to discriminate)")

# parametric CRC-32
def crc32_param(data,poly,init,refin,refout,xorout):
    def rev(x,n):
        r=0
        for _ in range(n): r=(r<<1)|(x&1); x>>=1
        return r
    crc=init
    for b in data:
        if refin: b=rev(b,8)
        crc^=b<<24
        for _ in range(8):
            crc=((crc<<1)^poly)&0xffffffff if (crc&0x80000000) else (crc<<1)&0xffffffff
    if refout: crc=rev(crc,32)
    return crc^xorout

CAT32=[ # name,poly,init,refin,refout,xorout
 ("CRC-32/ISO-HDLC(zlib)",0x04C11DB7,0xFFFFFFFF,1,1,0xFFFFFFFF),
 ("CRC-32/BZIP2",0x04C11DB7,0xFFFFFFFF,0,0,0xFFFFFFFF),
 ("CRC-32/MPEG-2",0x04C11DB7,0xFFFFFFFF,0,0,0x00000000),
 ("CRC-32/POSIX(cksum)",0x04C11DB7,0x00000000,0,0,0xFFFFFFFF),
 ("CRC-32/JAMCRC",0x04C11DB7,0xFFFFFFFF,1,1,0x00000000),
 ("CRC-32/XFER",0x000000AF,0x00000000,0,0,0x00000000),
 ("CRC-32C",0x1EDC6F41,0xFFFFFFFF,1,1,0xFFFFFFFF),
 ("CRC-32D",0xA833982B,0xFFFFFFFF,1,1,0xFFFFFFFF),
 ("CRC-32/AUTOSAR",0xF4ACFB13,0xFFFFFFFF,1,1,0xFFFFFFFF),
 ("CRC-32Q",0x814141AB,0x00000000,0,0,0x00000000),
 ("CRC-32/CKSUM-noxor",0x04C11DB7,0x00000000,0,0,0x00000000),
 ("CRC-32/init0-refl",0x04C11DB7,0x00000000,1,1,0x00000000),
]
def get_stored(c,endian):
    return struct.unpack('<I' if endian=='LE' else '>I',c['crc'])[0]

RANGES={'data':lambda c:c['data'],
        'hdr5+data':lambda c:c['all'],
        'tag..off+data':lambda c:c['hdr'][2:5]+c['data'],
        'data+hdr5':lambda c:c['data']+c['hdr']}
sample=chunks[:60]
print("\nbrute CRC-32 (match on first 60 chunks):")
hit=None
for rname,rf in RANGES.items():
    for endian in ('LE','BE'):
        for nm,poly,init,ri,ro,xo in CAT32:
            ok=all(crc32_param(rf(c),poly,init,ri,ro,xo)==get_stored(c,endian) for c in sample)
            if ok:
                print(f"  *** MATCH: {nm}  range={rname}  stored={endian}")
                hit=(nm,poly,init,ri,ro,xo,rname,endian,rf)
if hit:
    nm,poly,init,ri,ro,xo,rname,endian,rf=hit
    allok=all(crc32_param(rf(c),poly,init,ri,ro,xo)==get_stored(c,endian) for c in chunks)
    print(f"\nVALIDATE all {len(chunks)} chunks: {'ALL PASS' if allok else 'FAILED on full set'}")
else:
    print("  no CRC-32 catalog match — trying CRC-16 (low 2 bytes of field)")
    def crc16(data,poly,init,refin,refout,xorout):
        def rev(x,n):
            r=0
            for _ in range(n): r=(r<<1)|(x&1); x>>=1
            return r
        crc=init
        for b in data:
            if refin: b=rev(b,8)
            crc^=b<<8
            for _ in range(8):
                crc=((crc<<1)^poly)&0xffff if (crc&0x8000) else (crc<<1)&0xffff
        if refout: crc=rev(crc,16)
        return crc^xorout
    C16=[("CRC-16/CCITT-FALSE",0x1021,0xFFFF,0,0,0),
         ("CRC-16/XMODEM",0x1021,0x0000,0,0,0),
         ("CRC-16/ARC",0x8005,0x0000,1,1,0),
         ("CRC-16/MODBUS",0x8005,0xFFFF,1,1,0),
         ("CRC-16/KERMIT",0x1021,0x0000,1,1,0)]
    for rname,rf in RANGES.items():
        for lohi in (('LE16',lambda c:c['crc'][0]|(c['crc'][1]<<8)),
                     ('BE16',lambda c:(c['crc'][0]<<8)|c['crc'][1])):
            for nm,poly,init,ri,ro,xo in C16:
                if all(crc16(rf(c),poly,init,ri,ro,xo)==lohi[1](c) for c in sample):
                    print(f"  *** CRC-16 MATCH: {nm} range={rname} {lohi[0]}")
