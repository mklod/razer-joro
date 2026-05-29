"""
PHASE 0b/0c — sleep/wake control surface.

(A) Scan Synapse RUNTIME captures for power/idle commands. Razer
    Protocol30 power class is 0x07 (battery 0x80, charging 0x81,
    low-power 0x01, idle-time set/get 0x83/0x84 on many devices). Dump
    every class=0x07 frame + args, and anything that looks like an
    idle/timeout value.
(B) region 03 sleep points: every wfe/wfi/wfe-loop, its function, and
    nearby timeout constants (we saw #0x1f4=500, #0x7d0=2000 in
    0x0a970). Find where an idle counter is compared to a timeout and
    whether that timeout is loaded from the config/RAM (=> settable by
    a Protocol30 command) vs a hard immediate (=> needs FW patch).
"""
import struct, collections, bisect, glob, os
from scapy.all import rdpcap
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM

# ---- (A) runtime captures ----
RUNTIME=[
 r'L:\PROJECTS\razer-joro\captures\dongle_synapse_lighting_2026-04-24--1553_u1.pcap',
 r'L:\PROJECTS\razer-joro\captures\dongle_synapse_lighting_2026-04-24--1553_u2.pcap',
 r'L:\PROJECTS\razer-joro\captures\mode_toggle_u3.pcap',
 r'L:\PROJECTS\razer-joro\captures\synapse_hypershift_save_u2.pcap',
]
print("=== (A) class=0x07 (power/idle) + idle-looking commands in runtime captures ===")
for pc in RUNTIME:
    if not os.path.exists(pc): continue
    try: pkts=rdpcap(pc)
    except Exception as e:
        print(f"  {os.path.basename(pc)}: read err {e}"); continue
    frames=[]
    for p in pkts:
        raw=bytes(p)
        if len(raw)<27+8+90: continue
        so=None
        for off in range(20,min(40,len(raw)-8)):
            if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
        if so is None: continue
        if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
        rz=raw[so+8:so+8+90]
        frames.append((rz[6],rz[7],rz[5],rz[8:88]))
    h=collections.Counter((c,d) for c,d,_,_ in frames)
    p07=[(c,d,ds,a) for c,d,ds,a in frames if c==0x07]
    print(f"\n  {os.path.basename(pc)}: {len(frames)} frames; classes="
          f"{sorted(set(c for c,_,_,_ in frames))}")
    seen=set()
    for c,d,ds,a in p07:
        key=(d,ds,a[:8].hex())
        if key in seen: continue
        seen.add(key)
        print(f"    class=07 cmd=0x{d:02x} dsize={ds} args={a[:12].hex()}")

# ---- (B) region 03 sleep points ----
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

print("\n=== (B) sleep instructions (wfe/wfi/sev) in region 03 ===")
sleepfns=set()
for k,i in enumerate(ins):
    if i.mnemonic in ('wfe','wfi'):
        f=fo(i.address); sleepfns.add(f)
        ctx=' '.join(f"{x.mnemonic} {x.op_str}" for x in ins[max(0,k-3):k+2])
        print(f"  0x{i.address:05x} {i.mnemonic}  func 0x{(f or 0):05x}  | {ctx}")

# timeout-ish immediates (ms/sec values) and their functions
print("\n=== candidate idle/timeout immediates (movs/mov.w/cmp #N) ===")
cand=collections.defaultdict(list)
for i in ins:
    if i.mnemonic in ('mov.w','movw','movs','cmp.w','cmp') and i.operands:
        for op in i.operands:
            if op.type==ARM_OP_IMM and op.imm in (500,1000,2000,3000,5000,10000,
                                                  15000,30000,60000,0x1f4,0x7d0,
                                                  300,600,900,120,180,240,0xea60):
                cand[op.imm].append(i.address)
for v in sorted(cand):
    fs=sorted(set(fo(a) for a in cand[v]))
    print(f"  #{v} (0x{v:x}): {len(cand[v])} sites funcs="
          f"{[hex(x) for x in fs[:8]]}")

# does the sleep function read a timeout from RAM (settable) or use a
# hard immediate? show the main sleep function head
if sleepfns:
    sf=sorted(x for x in sleepfns if x is not None)
    for s in sf[:3]:
        print(f"\n--- sleep func 0x{s:05x} head ---")
        kk=next((j for j,x in enumerate(ins) if x.address==s),None)
        if kk is None: continue
        for x in ins[kk:kk+40]:
            print(f"  0x{x.address:05x}: {x.bytes.hex():<10s} {x.mnemonic} {x.op_str}")
            if (x.mnemonic=='pop' and 'pc' in x.op_str) or (x.mnemonic=='bx' and 'lr' in x.op_str): break
