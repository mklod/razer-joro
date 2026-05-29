"""
DECISIVE: find the true chunk data offset D using the hard invariant —
a valid Cortex-M vector table at the region-03 image base
(region 03 is flashed at address 0x0000 = the firmware base).

For the (tag, page=0, off=0) chunk of each region, and for D in 0..12,
read word0 (would-be initial SP) and word1 (would-be reset). The TRUE D
is the unique one where, for region 03:
  - word0 (SP) in nRF52 RAM range 0x20000000..0x20040000
  - word1 (reset) is odd (Thumb) and in flash 0x00000000..0x00100000
Then reconstruct region 03 at that D and report prologue density + a
short disassembly so we can see it's coherent. Settles: is there a
per-chunk CRC field at all, and was prior RE (D=9) misaligned?
"""
import struct
from scapy.all import rdpcap
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_LITTLE_ENDIAN

pk=rdpcap(r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap')
byreg={}
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
    tag,pg,off=a[2],a[3],a[4]
    byreg.setdefault(tag,[]).append({'a':a,'pg':pg,'off':off,'sz':sz})

for tag,lst in sorted(byreg.items()):
    base=min((c['pg']<<8)|c['off'] for c in lst)
    c0=next(c for c in lst if ((c['pg']<<8)|c['off'])==base)
    print(f"\n=== region {tag:02x}: {len(lst)} chunks, base addr 0x{base:04x} ===")
    print(f"  base chunk args[0:16] = {c0['a'][:16].hex()}")
    for D in range(0,13):
        w0=struct.unpack('<I',c0['a'][D:D+4])[0]
        w1=struct.unpack('<I',c0['a'][D+4:D+8])[0]
        sp_ok = 0x20000000<=w0<=0x20040000
        rst_ok= (w1&1)==1 and 0<=(w1&~1)<=0x00100000
        # also nRF52 'pointer table' tell: many words like 0x000274xx
        flag=' <== VALID VT' if (tag==0x03 and sp_ok and rst_ok) else (
              ' (sp-ok)' if sp_ok else '')
        print(f"   D={D:2d}: w0=0x{w0:08x} w1=0x{w1:08x}{flag}")

# reconstruct region 03 at the winning D (auto-pick) and sanity disasm
r3=sorted(byreg[0x03],key=lambda c:(c['pg']<<8)|c['off'])
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN)
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def density(D):
    b=b''.join(c['a'][D:D+c['sz']] for c in r3)
    n=sum(1 for o in range(0,len(b)-3,2)
          if (b[o+1]==0xb5 and b[o] in PRO) or (b[o]==0x2d and b[o+1]==0xe9))
    return b,n*1024/max(1,len(b))
print("\nregion03 prologue density by D:")
for D in (3,4,5,6,7,8,9):
    b,d=density(D)
    print(f"  D={D}: {d:.1f}/KB")
b5,_=density(5)
print(f"\nregion03 @D=5 head: SP=0x{struct.unpack('<I',b5[0:4])[0]:08x} "
      f"RESET=0x{struct.unpack('<I',b5[4:8])[0]:08x}")
print("  disasm @ reset&~1:")
rh=struct.unpack('<I',b5[4:8])[0]&~1
if rh<len(b5):
    for n,i in enumerate(md.disasm(b5[rh:rh+48],rh)):
        print(f"    {i.address:05x}: {i.mnemonic} {i.op_str}")
        if n>=11: break
else:
    print(f"   reset 0x{rh:x} outside region (image base != 0; expected)")
