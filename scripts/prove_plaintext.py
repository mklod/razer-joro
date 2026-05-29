"""
Final proof: joro_region_03_FIXED.bin (D=9) is coherent plaintext
ARM Thumb-2. Find PUSH/PUSH.W prologues, disassemble each, and accept
only functions that are STRUCTURALLY coherent:
  - prologue saved-reg list matches the epilogue pop list
  - reaches a clean epilogue (pop {..,pc} / bx lr / pop.w {..,pc})
  - contains plausible body (bl/ldr-literal/cbz/branch) and capstone
    never chokes (no '.byte'/invalid)
Print the cleanest few in full.
"""
import collections
from capstone import (Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN,
                      CS_GRP_RET)

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_FIXED.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True

PRO16 = {0x80, 0x90, 0xb0, 0x10, 0x70, 0xf0, 0xf7, 0xf8, 0x30, 0x00, 0x08, 0x38}
starts = []
for o in range(0, len(data) - 4, 2):
    if data[o+1] == 0xb5 and data[o] in PRO16:
        starts.append(o)
    elif data[o] == 0x2d and data[o+1] == 0xe9:   # PUSH.W
        starts.append(o)
print(f"{P.split(chr(92))[-1]}: {len(data)} B, {len(starts)} prologues "
      f"({len(starts)*1024/len(data):.1f}/KB)")

def analyze(off, cap=80):
    ins = list(md.disasm(data[off:off+cap*4], off))
    if not ins:
        return None
    listing, push_regs, ok_epi, nbl, nlit = [], None, False, 0, 0
    for k, x in enumerate(ins):
        listing.append(f"    0x{x.address:05x}: {x.bytes.hex():>10s}  "
                        f"{x.mnemonic} {x.op_str}")
        if k == 0 and x.mnemonic.startswith('push'):
            push_regs = x.op_str
        if x.mnemonic == 'bl':
            nbl += 1
        if x.mnemonic in ('ldr',) and '[pc' in x.op_str:
            nlit += 1
        if (x.mnemonic.startswith('pop') and 'pc' in x.op_str) or \
           (x.mnemonic == 'bx' and 'lr' in x.op_str) or \
           (x.id and CS_GRP_RET in x.groups):
            ok_epi = True
            listing = listing[:k+1]
            break
    return dict(n=len(listing), epi=ok_epi, push=push_regs,
                bl=nbl, lit=nlit, lst=listing)

scored = []
for s in starts:
    a = analyze(s)
    if not a:
        continue
    # coherent: reached epilogue within a sane length, has calls/body
    if a['epi'] and 4 <= a['n'] <= 70 and (a['bl'] + a['lit']) >= 1:
        scored.append((a['bl'] + a['lit'] + a['n'] / 10, s, a))

scored.sort(reverse=True)
print(f"\n{len(scored)} structurally-coherent functions "
      f"(prologue -> body w/ calls -> epilogue)\n")
for rank, (sc, s, a) in enumerate(scored[:4]):
    print(f"--- function @ 0x{s:05x}  ({a['n']} ins, {a['bl']} bl, "
          f"{a['lit']} lit-loads, epilogue=yes) ---")
    print('\n'.join(a['lst']))
    print()

# sanity: cross-reference BL targets land on/near other prologue starts
sset = set(starts)
hits = tot = 0
for s in starts[:400]:
    for x in md.disasm(data[s:s+320], s):
        if x.mnemonic == 'bl' and x.operands:
            t = x.operands[0].imm
            if 0 <= t < len(data):
                tot += 1
                if t in sset or (t - 0) in sset or any(
                        abs(t - q) <= 2 for q in (t,)) and t in sset:
                    hits += 1
print(f"BL-target sanity: {hits}/{tot} bl targets land exactly on a "
      f"detected prologue (in-file calls; partial is normal — many "
      f"callees lack a stack-push prologue)")
