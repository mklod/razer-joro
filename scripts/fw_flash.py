"""
Find the flash-write primitive in region 03 and whether the keymap /
Hypershift command path reaches it. Persistence question = reachability.

1. Locate flash primitives:
   - `svc #imm`  (SoftDevice: sd_flash_write/sd_flash_page_erase are SVCs)
   - any literal-pool word in nRF52 NVMC range 0x4001E000..0x4001EFFF
     (NVMC.READY 0x..400, CONFIG 0x..504, ERASEPAGE 0x..508, ERASEALL 50C)
   - FDS/fstorage tells: the strings 'fds'/'fstorage' won't be present but
     repeated 0x4001E504 + 0x4001E508 access in one func == raw NVMC driver
2. Build call graph (bl/blx, base=0 region 03).
3. Reverse-reachability: every function that can (transitively) reach a
   flash primitive -> mark FLASH-TAINTED.
4. Find command handlers for the keymap class: sites with immediate
   0x8d / 0xa4 / 0x0d / 0x03, and the dispatcher 0x0ae30 + its callees.
   Report which are FLASH-TAINTED (=> that command persists).
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True
PRO16 = {0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def is_pro(o): return o+1 < len(data) and ((data[o+1]==0xb5 and data[o] in PRO16) or (data[o]==0x2d and data[o+1]==0xe9))
funcs = sorted(o for o in range(0,len(data)-3,2) if is_pro(o))
def func_of(o):
    i = bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None

ins_at = {}
for o in range(0,len(data)-1,2):
    g = list(md.disasm(data[o:o+4],o))
    if g: ins_at[o]=g[0]

callg = collections.defaultdict(set)   # caller_func -> {callee_func/target}
revg  = collections.defaultdict(set)   # callee -> {caller_func}
svc_sites=[]; nvmc_sites=[]
for o,ins in ins_at.items():
    m=ins.mnemonic
    if m=='svc':
        svc_sites.append((o,ins.op_str,func_of(o)))
    if m in('bl','blx'):
        for op in ins.operands:
            if op.type==ARM_OP_IMM:
                t=op.imm; fo=func_of(o)
                callg[fo].add(t)
                if 0<=t<len(data): revg[t].add(fo)
    if m in('ldr','ldr.w'):
        for op in ins.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((ins.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data):
                    w=struct.unpack('<I',data[la:la+4])[0]
                    if 0x4001E000<=w<=0x4001EFFF:
                        nvmc_sites.append((o,w,func_of(o)))

print(f"region 03: {len(funcs)} funcs, {len(ins_at)} ins")
print(f"\nSVC (SoftDevice call) sites: {len(svc_sites)}")
for o,ops,fo in svc_sites[:30]:
    print(f"  0x{o:05x} svc {ops}  in func 0x{(fo or 0):05x}")
sc=collections.Counter(o for o,_,_ in [(s[1],0,0) for s in svc_sites])
print(f"\nNVMC (0x4001Exxx direct flash) sites: {len(nvmc_sites)}")
for o,w,fo in nvmc_sites:
    print(f"  0x{o:05x}  0x{w:08x}  in func 0x{(fo or 0):05x}")

# flash-primitive functions = funcs containing svc OR nvmc access
prim=set()
for _,_,fo in svc_sites:
    if fo is not None: prim.add(fo)
for _,_,fo in nvmc_sites:
    if fo is not None: prim.add(fo)
print(f"\nflash-primitive functions: {sorted(hex(x) for x in prim)}")

# reverse reachability
tainted=set(prim); frontier=set(prim)
while frontier:
    nxt=set()
    for f in frontier:
        for c in revg.get(f,()):
            if c is not None and c not in tainted:
                tainted.add(c); nxt.add(c)
    frontier=nxt
print(f"FLASH-TAINTED functions (can transitively write flash): {len(tainted)}")

# keymap/hypershift command immediates
imm=collections.defaultdict(list)
for o,ins in ins_at.items():
    if ins.mnemonic in('cmp','cmp.w','mov','movs','mov.w','movw','subs','sub.w','cmn'):
        for op in ins.operands:
            if op.type==ARM_OP_IMM: imm[op.imm].append(o)

print("\n=== keymap/Hypershift command handlers & flash-taint ===")
for v,label in ((0xa4,'unlock_keymap_writes'),(0x8d,'keymap entry r/w'),
                (0x0d,'set_layer_remap (Hypershift)'),(0x03,'class=keymap?'),
                (0x00,'txn begin/commit')):
    fs=collections.Counter(func_of(o) for o in imm.get(v,[]))
    print(f"\n cmd/val 0x{v:02x} ({label}) — {len(imm.get(v,[]))} sites:")
    for f,c in fs.most_common(8):
        if f is None: continue
        t='FLASH-TAINTED' if f in tainted else 'ram-only(no flash reach)'
        # does this func itself call a primitive directly?
        direct = 'DIRECT-FLASH' if f in prim else ''
        print(f"   func 0x{f:05x} x{c}  [{t}] {direct}")

# the 0x0ae30 dispatcher: list its bl callees and their taint
disp=0x0ae30
print(f"\n=== dispatcher 0x{disp:05x} callees ===")
for t in sorted(callg.get(disp,())):
    if 0<=t<len(data):
        tt='FLASH-TAINTED' if t in tainted else 'ram-only'
        pp='PROLOGUE' if is_pro(t) else ''
        print(f"  -> 0x{t:05x} {pp} [{tt}]")
    else:
        print(f"  -> 0x{t&0xffffffff:08x} EXTERN/other-region")
