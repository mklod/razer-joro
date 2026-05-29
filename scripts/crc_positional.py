"""
The args[5:9] field looks code-like and echoes nearby data bytes.
Test the "it's COPIED from the firmware stream, not computed" family:
for every chunk, does field == ...
  A: last 4 bytes of THIS chunk's data
  B: last 4 bytes of PREVIOUS chunk's data (same region, send order)
  C: first 4 bytes of NEXT chunk's data
  D: data[k:k+4] for some fixed k (sweep k=0..60)
  E: first 4 bytes of THIS data
  F: bytes right BEFORE data, i.e. it's a 13-byte header and data is
     really 60B? (sweep: field == data[-4:] under various data lens)
Also dump 6 consecutive chunks FULL (64B data + field) to eyeball the
real structure.
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
    a=bytes(rz[8:88]); sz=struct.unpack('<H',a[0:2])[0]
    seq.append({'a':a,'tag':a[2],'pg':a[3],'off':a[4],
                'field':a[5:9],'data':a[9:9+sz],'sz':sz,'args':a})
N=len(seq)
print(f"{N} chunks\n")

# full dump of 6 consecutive (region 02 start) chunks
print("=== 6 consecutive chunks: args[0:9] | full 64B data | tail ===")
for c in seq[:6]:
    print(f" hdr={c['a'][0:9].hex()}  field={c['field'].hex()}")
    print(f"   data[0:16] ={c['data'][:16].hex()}")
    print(f"   data[48:64]={c['data'][48:64].hex()}")

def cnt(pred):
    return sum(1 for i in range(N) if pred(i))

A=cnt(lambda i: seq[i]['field']==seq[i]['data'][-4:])
E=cnt(lambda i: seq[i]['field']==seq[i]['data'][:4])
B=cnt(lambda i: i>0 and seq[i]['field']==seq[i-1]['data'][-4:])
C=cnt(lambda i: i+1<N and seq[i]['field']==seq[i+1]['data'][:4])
print(f"\nA field==this.data[-4:]   {A}/{N}")
print(f"E field==this.data[:4]    {E}/{N}")
print(f"B field==prev.data[-4:]   {B}/{N}")
print(f"C field==next.data[:4]    {C}/{N}")

best=(0,-1)
for k in range(0,61):
    m=cnt(lambda i,k=k: len(seq[i]['data'])>=k+4 and seq[i]['field']==seq[i]['data'][k:k+4])
    if m>best[0]: best=(m,k)
print(f"D best fixed data[k:k+4]: k={best[1]} -> {best[0]}/{N}")

# treat field+data as one 68B blob; maybe real data = a[5:5+68] and the
# "header" is only 5 bytes. Check coherence: does a[5:73] disassemble?
from capstone import Cs,CS_ARCH_ARM,CS_MODE_THUMB,CS_MODE_LITTLE_ENDIAN
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN)
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
for D,name in ((5,'a[5:](field is data)'),(9,'a[9:](current model)')):
    blob=b''.join(c['args'][D:D+64] for c in seq if c['tag']==0x03)
    npro=sum(1 for o in range(0,len(blob)-3,2)
             if (blob[o+1]==0xb5 and blob[o] in PRO) or (blob[o]==0x2d and blob[o+1]==0xe9))
    print(f"region03 reconstructed at D={D} {name}: {len(blob)}B prologues "
          f"{npro*1024/max(1,len(blob)):.1f}/KB")
