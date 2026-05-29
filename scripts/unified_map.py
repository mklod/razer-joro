"""
Determine the absolute flash layout of regions 02/03/04 so region-03's
cross-region `bl` externs (0x12d40, 0x12e34, 0x13a44 forward;
0xffff754a, 0xffffb4c4, 0xffffd7b4 backward) resolve to real function
prologues. Same self-consistency metric that proved D=9: the correct
layout maximises region-03 bl-targets-land-on-a-detected-prologue
across the COMBINED image.

bl offsets are position-independent: if region 03 sits at absolute base
B3 in the combined image, a bl that capstone (region03@0) resolved to T
really targets (B3 + T) mod 2^32. Try the 6 orderings of the 3 region
blocks laid contiguously; for each, score region-03 bl resolution and
report where the key relax externs land.
"""
import struct, itertools, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM

REG = {t: open(rf'L:\PROJECTS\razer-joro\captures\joro_region_{t}.bin','rb').read()
       for t in ('02_at_0x7000','03_at_0x0000','04_at_0x0000')}
R02, R03, R04 = REG['02_at_0x7000'], REG['03_at_0x0000'], REG['04_at_0x0000']
BLOCKS = {'02': R02, '03': R03, '04': R04}
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN); md.detail = True
PRO16 = {0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}

def prologues(buf):
    s = set()
    for o in range(0, len(buf)-3, 2):
        if (buf[o+1]==0xb5 and buf[o] in PRO16) or (buf[o]==0x2d and buf[o+1]==0xe9):
            s.add(o)
    return s

# region-03 bl targets, as resolved by capstone with region03 @ 0
r3_bls = []   # (instr_off, target_T_with_r03_at_0)
o = 0
while o < len(R03)-1:
    g = list(md.disasm(R03[o:o+4], o))
    if not g: o += 2; continue
    i = g[0]
    if i.mnemonic in ('bl','blx'):
        for op in i.operands:
            if op.type == ARM_OP_IMM:
                r3_bls.append((i.address, op.imm & 0xffffffff))
    o += i.size
print(f"region03: {len(R03)} B, {len(r3_bls)} bl instructions")
KEY = [0x12d40,0x12e34,0x13a44,0xffff754a&0xffffffff,0xffffb4c4&0xffffffff,0xffffd7b4&0xffffffff]

best = None
for order in itertools.permutations(['02','03','04']):
    # contiguous layout; record each block's [base,end) and prologue set
    combined = b''
    spans = {}
    for t in order:
        spans[t] = (len(combined), len(combined)+len(BLOCKS[t]))
        combined += BLOCKS[t]
    B3 = spans['03'][0]
    pros = prologues(combined)
    tot = hit = 0
    keymap = {}
    for ioff, T in r3_bls:
        absT = (B3 + ((T ^ 0x80000000) - 0x80000000)) & 0xffffffff  # signed T then +B3
        tot += 1
        if 0 <= absT < len(combined) and absT in pros:
            hit += 1
        for k in KEY:
            kt = (B3 + ((k ^ 0x80000000) - 0x80000000)) & 0xffffffff
            keymap[k] = kt
    pct = 100*hit/tot if tot else 0
    # where do the key externs land (which region, on a prologue?)
    def loc(a):
        for t,(s,e) in spans.items():
            if s <= a < e:
                return f"r{t}+0x{a-s:05x}{'*PRO' if a in pros else ''}"
        return f"OOB(0x{a:08x})"
    kl = ' '.join(f"{k&0xffff:04x}->{loc((B3+((k^0x80000000)-0x80000000))&0xffffffff)}" for k in KEY)
    line = f"{'-'.join(order)}: B3=0x{B3:05x} bl-resolve {hit}/{tot} ({pct:.0f}%)  {kl}"
    print(line)
    if best is None or pct > best[0]:
        best = (pct, order, line)

print(f"\nBEST layout: {best[2]}")
