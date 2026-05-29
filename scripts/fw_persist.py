"""
Pin the exact keymap command that persists to flash.

- Recompute flash-primitive set + reverse reachability (svc / NVMC).
- For the class=0x03 keymap handler 0x0b844, decode BOTH tbb tables,
  bound them correctly, and for each command-case target print whether
  that code path is FLASH-TAINTED and the shortest call chain to the
  flash primitive.
- Also report taint of the specific callees seen in 0x0b844
  (0x1508, 0x1550, 0x163c, 0x552c) so we know which storage call writes.
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO16={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def is_pro(o): return o+1<len(data) and ((data[o+1]==0xb5 and data[o] in PRO16) or (data[o]==0x2d and data[o+1]==0xe9))
funcs=sorted(o for o in range(0,len(data)-3,2) if is_pro(o))
def func_of(o):
    i=bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None

ins_at={}
for o in range(0,len(data)-1,2):
    g=list(md.disasm(data[o:o+4],o))
    if g: ins_at[o]=g[0]

callg=collections.defaultdict(set); revg=collections.defaultdict(set)
prim=set()
for o,ins in ins_at.items():
    m=ins.mnemonic; fo=func_of(o)
    if m=='svc' and fo is not None: prim.add(fo)
    if m in('ldr','ldr.w'):
        for op in ins.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((ins.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data):
                    w=struct.unpack('<I',data[la:la+4])[0]
                    if 0x4001E000<=w<=0x4001EFFF and fo is not None: prim.add(fo)
    if m in('bl','blx'):
        for op in ins.operands:
            if op.type==ARM_OP_IMM:
                t=op.imm
                if fo is not None: callg[fo].add(t)
                if 0<=t<len(data): revg[t].add(fo)

# shortest path (BFS over callg) from src to any prim
def path_to_flash(src):
    if src in prim: return [src]
    seen={src}; q=collections.deque([(src,[src])])
    while q:
        f,pth=q.popleft()
        for t in callg.get(f,()):
            if not (0<=t<len(data)): continue
            tf=t if is_pro(t) else func_of(t)
            if tf is None or tf in seen: continue
            seen.add(tf)
            np=pth+[tf]
            if tf in prim: return np
            q.append((tf,np))
    return None

tainted=set(prim); fr=set(prim)
while fr:
    nx=set()
    for f in fr:
        for c in revg.get(f,()):
            if c is not None and c not in tainted: tainted.add(c); nx.add(c)
    fr=nx

print(f"prim(flash) funcs: {sorted(hex(x) for x in prim)}")
print(f"tainted: {len(tainted)} funcs\n")

for cal in (0x1508,0x1550,0x163c,0x552c,0x080d0,0x08464,0x00d40,0x00dd8):
    f=cal if is_pro(cal) else func_of(cal)
    pp=path_to_flash(f) if f is not None else None
    chain=' -> '.join(hex(x) for x in pp) if pp else 'NO flash path'
    print(f"  callee 0x{cal:05x} (func 0x{(f or 0):05x}): "
          f"{'FLASH' if f in tainted else 'ram-only'}  path: {chain}")

# decode 0x0b844 tbb tables with correct bounds and per-case taint
print("\n=== 0x0b844 command dispatch -> persistence ===")
def case_target(tbl,k): return tbl + 2*data[tbl+k]
# table 1 @0x0b898, indexed by (cmd-2), bound cmp #7 (cases 0..6 => cmd 2..8)
print(" table1 @0x0b898  index=(cmd-2), cmd 2..8:")
for k in range(0,7):
    tgt=case_target(0x0b898,k)
    f=tgt if is_pro(tgt) else func_of(tgt)
    # follow first bl out of tgt to judge taint of the handler body
    body_taint='ram-only'
    for o in range(tgt,min(tgt+120,len(data)),2):
        i=ins_at.get(o)
        if not i: continue
        if i.mnemonic in('bl','blx'):
            for op in i.operands:
                if op.type==ARM_OP_IMM and 0<=op.imm<len(data):
                    cf=op.imm if is_pro(op.imm) else func_of(op.imm)
                    if cf in tainted: body_taint='FLASH via 0x%05x'%op.imm
            break
        if i.mnemonic=='pop' and 'pc' in i.op_str: break
    print(f"  cmd 0x{k+2:02x} -> 0x{tgt:05x}  [{body_taint}]")

# table 2 @0x0b8c8 (high cmd range). Print first 16 cases w/ taint of target
print(" table2 @0x0b8c8 (high range), first 16 entries:")
for k in range(0,16):
    tgt=case_target(0x0b8c8,k)
    f=tgt if is_pro(tgt) else func_of(tgt)
    bt='ram-only'
    for o in range(tgt,min(tgt+120,len(data)),2):
        i=ins_at.get(o)
        if not i: continue
        if i.mnemonic in('bl','blx'):
            for op in i.operands:
                if op.type==ARM_OP_IMM and 0<=op.imm<len(data):
                    cf=op.imm if is_pro(op.imm) else func_of(op.imm)
                    if cf in tainted: bt='FLASH via 0x%05x'%op.imm
            break
        if i.mnemonic=='pop' and 'pc' in i.op_str: break
    print(f"  idx{k:2d} -> 0x{tgt:05x}  [{bt}]")
