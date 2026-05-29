"""
Close out the persistence question:
 1. Find every WRITER of the persist-enable flag 0x2000301e and the
    entry-gate 0x20003468 (and read sites): scan for `ldr rX,[pc,#imm]`
    whose literal == target (or target rounded), then within that
    function find str/strb/strh through rX -> that's a writer; the
    immediate stored = the value (e.g. the magic '2').
 2. Disassemble each writer function head + the store context, and
    whether the writer is a command handler (reads buf[6]/buf[7]).
 3. Fully disassemble 0x0c0a4 (the class-0xf0 flash-store) and annotate
    every load from the request buffer (0x2000b848) so we get the exact
    class-0xf0 packet layout the daemon would need to emit.
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import (ARM_OP_IMM, ARM_OP_MEM, ARM_OP_REG, ARM_REG_PC)

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def isp(o): return o+1<len(data) and ((data[o+1]==0xb5 and data[o] in PRO) or (data[o]==0x2d and data[o+1]==0xe9))
funcs=sorted(o for o in range(0,len(data)-3,2) if isp(o))
def fo(o):
    i=bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None

# linear instr map
ins=[]
o=0
while o < len(data)-1:
    g=list(md.disasm(data[o:o+4],o))
    if g:
        ins.append(g[0]); o+=g[0].size
    else:
        o+=2
by_addr={i.address:k for k,i in enumerate(ins)}

TARGETS={0x2000301e:'PERSIST-ENABLE flag', 0x20003468:'dispatcher entry-gate',
         0x2000b848:'Protocol30 request buf', 0x2000b850:'Protocol30 resp buf'}

def lit_of(i):
    if i.mnemonic in ('ldr','ldr.w'):
        for op in i.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((i.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data):
                    return struct.unpack('<I',data[la:la+4])[0]
    return None

# For each target, find ldr that loads it, the dest reg, then scan
# forward in same function for stores through that reg.
print("=== writers / readers of key RAM addresses ===")
for tgt,label in TARGETS.items():
    if tgt in (0x2000b848,0x2000b850):  # too many; skip detailed
        continue
    print(f"\n-- 0x{tgt:08x}  {label} --")
    for k,i in enumerate(ins):
        lv=lit_of(i)
        if lv is None: continue
        if lv==tgt or lv==(tgt& ~3) or abs(lv-tgt)<=4:
            # dest reg of the ldr
            dst=i.operands[0].reg if i.operands and i.operands[0].type==ARM_OP_REG else None
            fn=fo(i.address)
            iscmd=''
            # scan function window for str through dst
            verdict='read-only(no store via this reg seen)'
            store=None
            for j in range(k, min(k+24,len(ins))):
                jj=ins[j]
                if jj.mnemonic in ('str','strb','strh','str.w','strb.w','strh.w'):
                    ops=jj.operands
                    if len(ops)>=2 and ops[1].type==ARM_OP_MEM and ops[1].mem.base==dst:
                        src=ops[0]
                        sval='?'
                        # find a preceding movs/mov of src reg
                        for b in range(j,max(j-6,0),-1):
                            pb=ins[b]
                            if pb.mnemonic in('movs','mov','mov.w') and pb.operands and pb.operands[0].type==ARM_OP_REG and src.type==ARM_OP_REG and pb.operands[0].reg==src.reg and pb.operands[1].type==ARM_OP_IMM:
                                sval=f"#{pb.operands[1].imm}"; break
                        store=(jj.address,jj.mnemonic,jj.op_str,sval)
                        verdict=f"WRITER store@0x{jj.address:05x} {jj.mnemonic} {jj.op_str} (val {sval})"
                        break
                if jj.mnemonic=='pop' and 'pc' in jj.op_str: break
            print(f"  0x{i.address:05x} (func 0x{(fn or 0):05x}) ldr {i.op_str}  -> {verdict}")

# fully disasm a function
def dump(start,nmax=70,tag=''):
    print(f"\n=== {tag} func 0x{start:05x} ===")
    if start not in by_addr:
        print("  (not on instr boundary)"); return
    k=by_addr[start]
    while k<len(ins) and k<by_addr[start]+nmax:
        i=ins[k]; note=''
        lv=lit_of(i)
        if lv is not None:
            note=f"  ; lit=0x{lv:08x}"+(f" <{TARGETS[lv]}>" if lv in TARGETS else '')
        if i.mnemonic in('bl','blx','b','b.w'):
            for op in i.operands:
                if op.type==ARM_OP_IMM:
                    t=op.imm
                    note=f"  ; -> 0x{t&0xffffffff:05x}"+(' PRO' if 0<=t<len(data) and isp(t) else '')
        print(f"  0x{i.address:05x}: {i.bytes.hex():<10s} {i.mnemonic} {i.op_str}{note}")
        if (i.mnemonic=='pop' and 'pc' in i.op_str) or (i.mnemonic=='bx' and 'lr' in i.op_str):
            print("  [epilogue]"); break
        k+=1

dump(0x0c0a4, 70, 'class-0xf0 flash-store')
