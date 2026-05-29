"""
DECISIVE, single test: which D gives a SELF-CONSISTENT region-03 image?
Metric = fraction of `bl` instructions whose target lands EXACTLY on a
detected function prologue. Real correctly-aligned code ~ 40%+;
misaligned/garbage ~ a few %. Density can't tell D=5/7/9 apart (all
2-byte aligned); BL-coherence can. Whichever D maximises it is the true
chunk-data offset — settles both "is there a per-chunk CRC field" and
"was prior RE (D=9) misaligned".
"""
import struct
from scapy.all import rdpcap
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM

pk=rdpcap(r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap')
r3=[]
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
    a=bytes(rz[8:88]); sz=struct.unpack('<H',a[0:2])[0]
    if a[2]==0x03: r3.append((a,sz,(a[3]<<8)|a[4]))
r3.sort(key=lambda x:x[2])
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}

def analyze(D):
    data=b''.join(a[D:D+sz] for a,sz,_ in r3)
    pro=set()
    for o in range(0,len(data)-3,2):
        if (data[o+1]==0xb5 and data[o] in PRO) or (data[o]==0x2d and data[o+1]==0xe9):
            pro.add(o)
    tot=hit=0
    # linear sweep; count bl whose imm target is a detected prologue
    for o in range(0,len(data)-3,2):
        for ins in md.disasm(data[o:o+4],o):
            if ins.mnemonic=='bl':
                for op in ins.operands:
                    if op.type==ARM_OP_IMM:
                        t=op.imm
                        if 0<=t<len(data):
                            tot+=1
                            if t in pro: hit+=1
            break
    pct=100*hit/tot if tot else 0
    return len(pro),tot,hit,pct

print("D :  prologues  bl   on-prologue   %  (true offset = the clear max)")
res=[]
for D in (3,5,7,9):
    npro,tot,hit,pct=analyze(D)
    res.append((pct,D))
    print(f"D={D}:  {npro:5d}   {tot:5d}   {hit:5d}     {pct:5.1f}%")
res.sort(reverse=True)
win=res[0][1]
print(f"\n=> highest BL-coherence at D={win} "
      f"({'D=9 = prior RE was correct; args[5:9] IS a separate field' if win==9 else f'D={win} = prior RE MISALIGNED; re-extract; likely NO per-chunk CRC'})")
