"""
Find what TRIGGERS the keymap RAM->flash flush (caller chain of 0x0598e).

- Build full bl/blx call graph (region 03, base 0).
- Reverse-trace callers of 0x0598e (and 0x05c80) up to 6 levels; mark
  any caller that is a command handler (reads buf[6]/buf[7]) or looks
  like an event/timer callback (no in-region callers => registered as
  a function pointer with the SoftDevice / scheduler).
- Disassemble 0x0598e itself: what RAM source it reads, any dirty-flag
  gate, whether it loops over keymap entries.
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def isp(o): return o+1<len(data) and ((data[o+1]==0xb5 and data[o] in PRO) or (data[o]==0x2d and data[o+1]==0xe9))
funcs=sorted(o for o in range(0,len(data)-3,2) if isp(o))
def fo(o):
    i=bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None

ins_at={}
for o in range(0,len(data)-1,2):
    g=list(md.disasm(data[o:o+4],o))
    if g: ins_at[o]=g[0]

callg=collections.defaultdict(set); rev=collections.defaultdict(set)
reads67=set()           # funcs that read [r,#6] and [r,#7]
for o,ins in ins_at.items():
    m=ins.mnemonic; f=fo(o)
    if m in('bl','blx'):
        for op in ins.operands:
            if op.type==ARM_OP_IMM:
                t=op.imm
                if f is not None: callg[f].add(t)
                if 0<=t<len(data): rev[t if isp(t) else fo(t)].add(f)
    if m in('ldrb','ldrb.w'):
        s=ins.op_str.replace(' ','')
        if s.endswith('#6]') and f is not None: reads67.add(('6',f))
        if s.endswith('#7]') and f is not None: reads67.add(('7',f))
cmdh={f for k,f in reads67 if k=='6'} & {f for k,f in reads67 if k=='7'}

def kind(f):
    callers=[c for c in rev.get(f,()) if c is not None]
    tags=[]
    if f in cmdh: tags.append('CMD-HANDLER(reads buf[6]&[7])')
    if not callers: tags.append('NO-in-region-callers => EVENT/TIMER/ptr-callback')
    return ' '.join(tags) or f'{len(callers)} caller(s)'

def up(f,depth,seen,ind):
    if f in seen or ind>depth: return
    seen.add(f)
    for c in sorted(x for x in rev.get(f,()) if x is not None):
        print('  '*ind+f'<- 0x{c:05x}  [{kind(c)}]')
        up(c,depth,seen,ind+1)

for tgt in (0x0598e,0x05c80,0x05954,0x05976):
    f=tgt if isp(tgt) else fo(tgt)
    print(f"\n=== caller tree of 0x{tgt:05x} (func 0x{(f or 0):05x}) [{kind(f)}] ===")
    up(f,5,set(),1)

print("\n=== disasm 0x0598e (flush fn) ===")
cnt=0; addr=0x0598e
while cnt<70 and addr<len(data):
    g=list(md.disasm(data[addr:addr+4],addr))
    if not g:
        addr+=2; continue
    i=g[0]; note=''
    if i.mnemonic in('bl','blx','b','b.w'):
        for op in i.operands:
            if op.type==ARM_OP_IMM:
                t=op.imm
                note=f"  ; -> 0x{t&0xffffffff:05x}"+(' PRO' if 0<=t<len(data) and isp(t) else '')
    if i.mnemonic in('ldr','ldr.w'):
        for op in i.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((i.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data):
                    note=f"  ; lit=0x{struct.unpack('<I',data[la:la+4])[0]:08x}"
    print(f"  0x{i.address:05x}: {i.bytes.hex():<10s} {i.mnemonic} {i.op_str}{note}")
    cnt+=1
    if (i.mnemonic=='pop' and 'pc' in i.op_str) or (i.mnemonic=='bx' and 'lr' in i.op_str):
        print("  [epilogue]"); break
    addr+=i.size
