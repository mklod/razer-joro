"""
Phase 3-pre / option B: find the dongle (HyperSpeed) re-pair mechanism
in region-03 firmware.

Approach:
 1. Re-walk the class dispatcher 0x0bb88 (class = request[6]); enumerate
    every class -> handler (both tbb tables + the 0xf0/0xfe tail).
 2. For each class handler, scan its body (and 1 level of callees) for
    pairing tells: SoftDevice BLE-GAP/SM SVCs, "delete all bonds" /
    advertising-restart patterns, radio/ESB peripheral writes
    (0x40001000 RADIO), or references to a pairing/bond state byte.
 3. Also: list ALL svc numbers in region 03 grouped by function, and
    flag functions that look like "enter pairing": clear bond store +
    (re)start advertising/radio.
Output: ranked pairing-handler candidates (class:cmd + address).
"""
import struct, collections, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

P=r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data=open(P,'rb').read()
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def isp(o): return o+1<len(data) and ((data[o+1]==0xb5 and data[o] in PRO) or (data[o]==0x2d and data[o+1]==0xe9))
funcs=sorted(o for o in range(0,len(data)-3,2) if isp(o))
def fo(o):
    i=bisect.bisect_right(funcs,o)-1
    return funcs[i] if i>=0 else None
ins={}; o=0
while o<len(data)-1:
    g=list(md.disasm(data[o:o+4],o))
    if g: ins[g[0].address]=g[0]; o+=g[0].size
    else: o+=2
addrs=sorted(ins)

callg=collections.defaultdict(list)
svcs=collections.defaultdict(list)        # func -> [svc#]
radio=collections.defaultdict(list)       # func -> [periph addr]
for a,i in ins.items():
    f=fo(a)
    if i.mnemonic in('bl','blx'):
        for op in i.operands:
            if op.type==ARM_OP_IMM: callg[f].append(op.imm)
    if i.mnemonic=='svc':
        try: svcs[f].append(int(i.op_str.replace('#',''),0))
        except: pass
    if i.mnemonic in('ldr','ldr.w'):
        for op in i.operands:
            if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                la=((i.address+4)&~3)+op.mem.disp
                if 0<=la+4<=len(data):
                    w=struct.unpack('<I',data[la:la+4])[0]
                    # nRF52 RADIO 0x40001000, ESB/clock 0x40000000, ficr/uicr
                    if w in (0x40001000,0x40001504,0x10001000) or 0x40001000<=w<0x40002000:
                        radio[f].append(w)

# decode tbb at addr -> list of (case, target)
def tbb_targets(tbb_addr, n):
    tbl=tbb_addr+2  # tbb is 2 bytes (T1) ... actually 4 for dfe8; handle both
    op=ins.get(tbb_addr)
    sz=op.size if op else 2
    tbl=tbb_addr+sz
    out=[]
    for k in range(n):
        e=data[tbl+k]
        out.append((k, tbl+2*e))
    return out

print("=== class dispatcher 0x0bb88: class -> handler ===")
# from prior RE: r1=req[6]=class; tbb @0x0bbb0 (classes 0..5);
# tail compares handle 0xf0/0xfe.
for k,t in tbb_targets(0x0bbb0, 7):
    f=t if isp(t) else fo(t)
    # follow first bl from the case target
    callee=None
    for a in addrs:
        if a<t: continue
        if a>t+40: break
        ii=ins[a]
        if ii.mnemonic in('bl','blx'):
            for op in ii.operands:
                if op.type==ARM_OP_IMM: callee=op.imm
            break
    cf = callee if callee is not None and isp(callee) else (fo(callee) if callee is not None else None)
    sv = svcs.get(cf,[]) if cf is not None else []
    rd = radio.get(cf,[]) if cf is not None else []
    tag=''
    if rd: tag+=' RADIO!'
    if any(s not in (0x28,0x29) for s in sv): tag+=f' svc={[hex(s) for s in sv]}'
    print(f"  class {k}: case@0x{t:05x} -> handler 0x{(cf or 0):05x}{tag}")

# all SVCs by function — flag BLE-ish (not the flash 0x28/0x29 we know)
print("\n=== functions using non-flash SVCs (SoftDevice BLE/GAP/SM/radio) ===")
known_flash={0x28,0x29}
rows=[]
for f,sl in svcs.items():
    nf=sorted(set(s for s in sl if s not in known_flash))
    if nf: rows.append((f,nf,len(callg.get(f,[]))))
for f,nf,nc in sorted(rows)[:40]:
    rd=' RADIO' if f in radio else ''
    print(f"  func 0x{f:05x}: svc {[hex(s) for s in nf]}{rd}")

print("\n=== functions touching nRF52 RADIO/ESB (0x40001xxx) — pairing/link ===")
for f in sorted(radio):
    print(f"  func 0x{f:05x}: {sorted(set(hex(x) for x in radio[f]))}  "
          f"svc={[hex(s) for s in sorted(set(svcs.get(f,[])))]}")
