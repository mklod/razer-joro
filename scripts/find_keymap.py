"""
Thorough hunt for the base keymap. The QWERTYUIOP HID run
(14 1a 08 15 17 1c 18 0c 12 13) and ASDFGHJKL (04 16 07 09 0a 0b 0d 0e 0f)
must appear if the firmware stores HID usages. Search:
  - raw fwupdate_stock_replay.bin (all 10:02 chunk data concatenated, D=5
    AND D=9 reconstructions) — D-agnostic
  - strides 1..4, and also reversed / +0x80 (some keymaps set bit7)
  - also try SCANCODE set-1 representation of QWERTY (10 11 12 13 14 15
    16 17 18 19) in case it stores PS/2 scancodes
Report every region/offset/stride hit so we can patch Z by content.
"""
import struct

def hid(s):
    m={'A':4,'B':5,'C':6,'D':7,'E':8,'F':9,'G':10,'H':11,'I':12,'J':13,
       'K':14,'L':15,'M':16,'N':17,'O':18,'P':19,'Q':20,'R':21,'S':22,
       'T':23,'U':24,'V':25,'W':26,'X':27,'Y':28,'Z':29}
    return bytes(m[c] for c in s)
QWERTY=hid("QWERTYUIOP")
ASDF=hid("ASDFGHJKL")
ZXCV=hid("ZXCVBNM")
SC1_QWERTY=bytes([0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,0x18,0x19]) # PS/2 set1
print(f"QWERTY hid={QWERTY.hex()}  ASDF={ASDF.hex()}  ZXCVBNM={ZXCV.hex()}")

pk_blob=open(r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin','rb').read()
chunks=[]
for i in range(0,len(pk_blob),90):
    f=pk_blob[i:i+90]
    if f[6]==0x10 and f[7]==0x02:
        a=f[8:88]; sz=struct.unpack('<H',a[0:2])[0]
        chunks.append((a[2],a[3],a[4],a))   # tag,page,off,args(80)

def recon(D):
    # per region: order chunks by addr, concat args[D:D+64]
    from collections import defaultdict
    reg=defaultdict(list)
    for tag,pg,off,a in chunks:
        reg[tag].append(((pg<<8)|off,a[D:D+64]))
    out={}
    for tag,lst in reg.items():
        lst.sort()
        out[tag]=b''.join(x[1] for x in lst)
    return out

def scan(name,data):
    res=[]
    for label,pat in (('QWERTY',QWERTY),('ASDF',ASDF),('ZXCVBNM',ZXCV),
                      ('SC1',SC1_QWERTY)):
        for stride in (1,2,3,4):
            n=len(pat)
            for o in range(0,len(data)-n*stride):
                if all(data[o+k*stride]==pat[k] for k in range(n)):
                    res.append((label,stride,o))
                    if len(res)>12: return res
    return res

for D in (5,9):
    rec=recon(D)
    for tag,data in sorted(rec.items()):
        r=scan(f"D{D}-r{tag:02x}",data)
        if r:
            for lab,st,o in r:
                # show context
                ctx=data[max(0,o-4):o+ (len(QWERTY)*st)+4].hex()
                print(f"  D={D} region 0x{tag:02x}: {lab} stride={st} @0x{o:05x}  ctx={ctx}")
# also raw: search each chunk's full 80 args (D-agnostic, in-chunk only)
print("raw per-chunk args scan (QWERTY any stride):")
for idx,(tag,pg,off,a) in enumerate(chunks):
    for st in (1,2,3,4):
        for o in range(0,80-len(QWERTY)*st):
            if all(a[o+k*st]==QWERTY[k] for k in range(len(QWERTY))):
                print(f"  chunk[{idx}] tag=0x{tag:02x} pg=0x{pg:02x} off=0x{off:02x} "
                      f"args+0x{o:02x} stride={st}  ctx={a[max(0,o-3):o+len(QWERTY)*st+3].hex()}")
