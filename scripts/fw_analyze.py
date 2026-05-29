"""
Joro firmware static-analysis foundation (capstone, plaintext D=9 regions).

Builds a navigable map of region 03 (main app, base treated as 0x0):
  - function table (prologue scan + bl-target seeds), call graph
  - string table + xrefs (PC-relative ldr literal -> .ascii)
  - Thumb-2 table-branch (tbb/tbh) switch detection  <-- protocol dispatch
  - immediate-constant index (find cmp #0xa4 / class/cmd compares)
  - MCU id: scan literal pools for known flash-controller bases
            (nRF52 NVMC 0x4001E000/0x4001E504, STM32 FLASH 0x40022000,
             0x40023C00, Nuvoton 0x4000C000, etc.)

Usage:
  fw_analyze.py            -> overview + dispatcher hunt
  fw_analyze.py func 0xADDR -> disassemble one function w/ xref annotation
  fw_analyze.py imm 0xA4    -> every site comparing/loading that immediate
"""
import sys, struct, collections
from capstone import (Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN,
                      CS_GRP_JUMP, CS_GRP_CALL, CS_GRP_RET)
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

REG = {
    '02': r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin',
    '03': r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin',
    '04': r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin',
}
data = open(REG['03'], 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True

PRO16 = {0x80, 0x90, 0xb0, 0x10, 0x70, 0xf0, 0xf7, 0xf8, 0x30, 0x00, 0x08, 0x38}

def is_prologue(o):
    return (o + 1 < len(data) and
            ((data[o + 1] == 0xb5 and data[o] in PRO16) or
             (data[o] == 0x2d and data[o + 1] == 0xe9)))

# ---- linear sweep, collect instructions, bl/b targets, literal loads ----
def sweep():
    ins_at = {}
    for o in range(0, len(data) - 1, 2):
        for ins in md.disasm(data[o:o + 4], o):
            ins_at[o] = ins
            break
    return ins_at

print("disassembling region 03 ...")
ins_at = sweep()

# functions: prologue offsets
funcs = sorted(o for o in range(0, len(data) - 3, 2) if is_prologue(o))
fset = set(funcs)

# call graph + literal pool string xrefs
callers = collections.defaultdict(set)   # callee -> {caller_func}
calls = collections.defaultdict(list)    # func -> [(site, target)]
litrefs = collections.defaultdict(list)  # str_off -> [site]
imm_sites = collections.defaultdict(list)  # imm -> [(off, mnem)]

def func_of(off):
    # nearest prologue at or before off
    import bisect
    i = bisect.bisect_right(funcs, off) - 1
    return funcs[i] if i >= 0 else None

def ldr_literal_addr(ins):
    # ldr rX, [pc, #imm]  -> literal address (Thumb: (PC&~3)+4+imm)
    if ins.mnemonic not in ('ldr', 'ldr.w'):
        return None
    for op in ins.operands:
        if op.type == ARM_OP_MEM and op.mem.base == ARM_REG_PC:
            return ((ins.address + 4) & ~3) + op.mem.disp
    return None

for off, ins in ins_at.items():
    m = ins.mnemonic
    # calls / branches
    if m in ('bl', 'blx', 'b', 'b.w', 'bx') or m.startswith('cb'):
        for op in ins.operands:
            if op.type == ARM_OP_IMM:
                t = op.imm
                if m in ('bl', 'blx'):
                    fo = func_of(off)
                    if fo is not None:
                        calls[fo].append((off, t))
                    if 0 <= t < len(data):
                        callers[t].add(fo)
    # immediate constants (cmp/mov/movw/sub/add/cmn)
    if m in ('cmp', 'cmp.w', 'mov', 'movs', 'mov.w', 'movw', 'subs',
             'sub.w', 'cmn', 'teq', 'and', 'orr', 'eor'):
        for op in ins.operands:
            if op.type == ARM_OP_IMM and 0 <= op.imm <= 0xffffffff:
                imm_sites[op.imm].append((off, f"{m} {ins.op_str}"))
    # literal pool -> string xref
    la = ldr_literal_addr(ins)
    if la is not None and 0 <= la + 4 <= len(data):
        ptr = struct.unpack('<I', data[la:la + 4])[0] if la + 4 <= len(data) else None
        # if the loaded word points into this region and looks like ascii, record
        if ptr is not None and 0 <= ptr < len(data):
            s = data[ptr:ptr + 48]
            end = s.find(b'\x00')
            cand = s[:end if end != -1 else 48]
            if len(cand) >= 4 and all(0x20 <= b < 0x7f for b in cand):
                litrefs[ptr].append(off)

# ---- table-branch (switch) detection: tbb/tbh ----
switches = []
for off, ins in ins_at.items():
    if ins.mnemonic in ('tbb', 'tbh'):
        switches.append((off, ins.mnemonic, ins.op_str))

# ---- MCU id via flash-controller register bases in literal pools ----
KNOWN = {
    0x4001E000: 'nRF52 NVMC base', 0x4001E504: 'nRF52 NVMC.CONFIG',
    0x4001E508: 'nRF52 NVMC.ERASEPAGE', 0x10001000: 'nRF52 UICR',
    0x40022000: 'STM32F1 FLASH', 0x40023C00: 'STM32F4 FLASH',
    0x40004000: 'STM32 IWDG?', 0x4000C000: 'Nuvoton FMC',
    0x50000000: 'nRF52 GPIO P0', 0x40000000: 'nRF52 CLOCK/POWER',
    0xE000ED00: 'SCB CPUID', 0x40001000: 'nRF UARTE0',
}
words = collections.Counter()
for o in range(0, len(data) - 3, 4):
    w = struct.unpack('<I', data[o:o + 4])[0]
    if (w & 0xFFFF0000) in (0x40000000, 0x50000000, 0x10000000, 0xE0000000):
        words[w] += 1

print(f"\n=== region 03: {len(data)} B, {len(funcs)} functions ===")
print(f"table-branch switches (tbb/tbh): {len(switches)}")
for o, mn, ops in switches[:20]:
    fo = func_of(o)
    print(f"  0x{o:05x}  {mn} {ops}   (in func 0x{(fo or 0):05x})")

print(f"\nstrings referenced from code ({len(litrefs)}):")
shown = 0
for ptr in sorted(litrefs):
    s = data[ptr:ptr + 48]
    end = s.find(b'\x00')
    txt = s[:end if end != -1 else 48].decode('ascii', 'replace')
    print(f"  0x{ptr:05x} x{len(litrefs[ptr])}  {txt!r}")
    shown += 1
    if shown >= 40:
        print(f"  ... (+{len(litrefs) - shown} more)")
        break

print(f"\nperipheral/flash register constants in literal pools:")
for w, c in words.most_common(30):
    tag = KNOWN.get(w, KNOWN.get(w & 0xFFFFF000, ''))
    mark = f"  <-- {tag}" if tag else ''
    print(f"  0x{w:08x}  x{c}{mark}")

# ---- dispatcher hunt: functions that read buf[6] & buf[7] (class,cmd) ----
print(f"\n=== Protocol30 dispatcher candidates "
      f"(read byte +6 then +7 of a pointer) ===")
hits = []
offs_sorted = sorted(ins_at)
for idx, o in enumerate(offs_sorted):
    ins = ins_at[o]
    if ins.mnemonic in ('ldrb', 'ldrb.w') and '#6]' in ins.op_str.replace(' ', ''):
        # look ahead ~12 instrs for a ldrb [..,#7]
        for o2 in offs_sorted[idx + 1: idx + 14]:
            i2 = ins_at[o2]
            if i2.mnemonic in ('ldrb', 'ldrb.w') and '#7]' in i2.op_str.replace(' ', ''):
                hits.append((o, func_of(o)))
                break
seen = set()
for o, fo in hits:
    if fo in seen:
        continue
    seen.add(fo)
    ncmp = sum(1 for s, _ in imm_sites.get(0xa4, []) if func_of(s) == fo)
    print(f"  func 0x{(fo or 0):05x}: reads +6/+7 @0x{o:05x} "
          f"(callers={len(callers.get(fo, []))})")

# immediate index for the key command bytes
print(f"\n=== command-byte immediates (dispatch tells) ===")
for v in (0xa4, 0x8d, 0x0d, 0x10, 0x05, 0x04, 0x0b, 0x80, 0x07, 0x02, 0x01):
    sites = imm_sites.get(v, [])
    fs = collections.Counter(func_of(s) for s, _ in sites)
    top = ', '.join(f"0x{(f or 0):05x}×{c}" for f, c in fs.most_common(5))
    print(f"  #0x{v:02x}: {len(sites)} sites  funcs: {top}")
