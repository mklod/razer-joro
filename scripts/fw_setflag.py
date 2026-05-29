"""
Which command/event sets the persist-enable flag 0x2000301e to the
required value 2? For every store through a pointer == 0x2000301e,
resolve the EXACT stored value by backward dataflow (movs/mov/mov.w,
uxtb, and reg->reg copies), and classify the writer function:
  - reads buf[6] (class) and/or buf[7] (cmd)  => command handler
  - no in-region callers                       => event/timer callback
Then disassemble the writer(s) that store value 2 (the prize) and the
ones that store 0 (the disable, = Synapse "turns it off over BLE").
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC

P=r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data=open(P,'rb').read()
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def isp(o): return o+1<len(data) and ((data[o+1]==0xb5 and data[o] in PRO) or (data[o]==0x2d and data[o+1]==0xe9))
funcs=sorted(o for o in range(0,len(data)-3,2) if isp(o))
def fo(o):
    i=bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None
ins=[]; o=0
while o<len(data)-1:
    g=list(md.disasm(data[o:o+4],o))
    if g: ins.append(g[0]); o+=g[0].size
    else: o+=2
idx={i.address:k for k,i in enumerate(ins)}

callers=collections.defaultdict(set)
reads=collections.defaultdict(set)   # func -> {6,7}
for i in ins:
    f=fo(i.address)
    if i.mnemonic in('bl','blx'):
        for op in i.operands:
            if op.type==ARM_OP_IMM and 0<=op.imm<len(data):
                callers[op.imm if isp(op.imm) else fo(op.imm)].add(f)
    if i.mnemonic in('ldrb','ldrb.w'):
        s=i.op_str.replace(' ','')
        if s.endswith('#6]') and f is not None: reads[f].add(6)
        if s.endswith('#7]') and f is not None: reads[f].add(7)

def lit(i):
    if i.mnemonic in('ldr','ldr.w'):
        for op in i.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((i.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data): return struct.unpack('<I',data[la:la+4])[0]
    return None

TGT=0x2000301e
def val_of(k,reg):
    """backward-resolve immediate in `reg` at instruction index k."""
    for b in range(k-1,max(k-12,0),-1):
        p=ins[b]
        if not p.operands: continue
        d=p.operands[0]
        if d.type==ARM_OP_REG and d.reg==reg:
            if p.mnemonic in('movs','mov','mov.w','movw') and len(p.operands)>1 and p.operands[1].type==ARM_OP_IMM:
                return p.operands[1].imm
            if p.mnemonic in('uxtb','uxth','mov') and len(p.operands)>1 and p.operands[1].type==ARM_OP_REG:
                return val_of(b,p.operands[1].reg)
            return None
    return None

print(f"=== stores to *0x{TGT:08x} (persist-enable flag) ===")
rows=[]
for k,i in enumerate(ins):
    lv=lit(i)
    if lv is None or not (lv==TGT or abs(lv-TGT)<=4): continue
    dst=i.operands[0].reg if i.operands and i.operands[0].type==ARM_OP_REG else None
    fn=fo(i.address); base_off=TGT-lv
    for j in range(k,min(k+20,len(ins))):
        s=ins[j]
        if s.mnemonic.startswith(('str','strb','strh')) and len(s.operands)>=2 and s.operands[1].type==ARM_OP_MEM and s.operands[1].mem.base==dst:
            disp=s.operands[1].mem.disp
            if disp+ (lv) - TGT != 0 and disp!=base_off:  # not the flag byte
                continue
            sv=val_of(j,s.operands[0].reg) if s.operands[0].type==ARM_OP_REG else None
            tags=[]
            r=reads.get(fn,set())
            if r: tags.append('cmd-handler reads buf'+''.join(f'[{x}]' for x in sorted(r)))
            if not [c for c in callers.get(fn,()) if c is not None]: tags.append('EVENT/callback(no in-region callers)')
            rows.append((s.address,fn,s.mnemonic,s.op_str,sv,' '.join(tags)))
            break
        if s.mnemonic=='pop' and 'pc' in s.op_str: break
for a,fn,mn,ops,sv,tg in rows:
    svs = f"={sv}" if sv is not None else "=?"
    star=' <<< SETS 2 (PERSIST-ENABLE)' if sv==2 else (' (disable=0)' if sv==0 else '')
    print(f"  0x{a:05x} func 0x{(fn or 0):05x}  {mn} {ops}  val{svs}  [{tg}]{star}")

# disasm any writer that stores 2, plus its caller context
def dump(start,n=46,tag=''):
    if start not in idx:
        print(f"\n(0x{start:05x} not on boundary)"); return
    print(f"\n=== {tag} 0x{start:05x}  callers={sorted(hex(c) for c in callers.get(start,()) if c is not None)} reads={sorted(reads.get(start,()))} ===")
    k=idx[start]
    while k<len(ins) and k<idx[start]+n:
        i=ins[k]; nb=''
        lv=lit(i)
        if lv is not None: nb=f"  ; lit=0x{lv:08x}"+(' <FLAG>' if abs(lv-TGT)<=4 else '')
        if i.mnemonic in('bl','blx','b','b.w'):
            for op in i.operands:
                if op.type==ARM_OP_IMM: nb=f"  ; -> 0x{op.imm&0xffffffff:05x}"+(' PRO' if 0<=op.imm<len(data) and isp(op.imm) else '')
        print(f"  0x{i.address:05x}: {i.bytes.hex():<10s} {i.mnemonic} {i.op_str}{nb}")
        if (i.mnemonic=='pop' and 'pc' in i.op_str) or (i.mnemonic=='bx' and 'lr' in i.op_str): print("  [epilogue]"); break
        k+=1

for a,fn,mn,ops,sv,tg in rows:
    if sv==2: dump(fn,52,'SETS-2 writer')
