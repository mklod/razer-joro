"""
Disassemble one region-03 function with rich annotation:
  - bl/blx target -> (offset, 'PROLOGUE' if a known func start)
  - ldr rX,[pc,#imm] -> the loaded 32-bit word (hex) + ascii if printable
  - tbb/tbh -> FULLY DECODE the following jump table: each case index ->
    absolute target offset, flagged if it lands on a prologue. This is
    how the Protocol30 class/cmd dispatch maps to handlers.

Usage: fw_func.py 0xADDR [ninstr]
"""
import sys, struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True

PRO16 = {0x80, 0x90, 0xb0, 0x10, 0x70, 0xf0, 0xf7, 0xf8, 0x30, 0x00, 0x08, 0x38}
def is_prologue(o):
    return o + 1 < len(data) and ((data[o+1] == 0xb5 and data[o] in PRO16)
                                  or (data[o] == 0x2d and data[o+1] == 0xe9))

start = int(sys.argv[1], 0)
nmax = int(sys.argv[2], 0) if len(sys.argv) > 2 else 160

def ascii_at(addr):
    if 0 <= addr < len(data):
        s = data[addr:addr+40]
        e = s.find(b'\x00')
        c = s[:e if e != -1 else 40]
        if len(c) >= 3 and all(0x20 <= b < 0x7f for b in c):
            return c.decode('ascii')
    return None

print(f"=== func 0x{start:05x} (region 03, base=0) ===")
end = start + nmax * 4
addr = start
count = 0
while addr < min(end, len(data)) and count < nmax:
    chunk = data[addr:addr+4]
    got = list(md.disasm(chunk, addr))
    if not got:
        print(f"  0x{addr:05x}: {data[addr:addr+2].hex()}    .short")
        addr += 2
        count += 1
        continue
    ins = got[0]
    note = ''
    m = ins.mnemonic

    if m in ('bl', 'blx', 'b', 'b.w') :
        for op in ins.operands:
            if op.type == ARM_OP_IMM:
                t = op.imm
                inrange = 0 <= t < len(data)
                tag = 'PROLOGUE' if inrange and is_prologue(t) else (
                      'in-region' if inrange else 'EXTERN/other-region')
                note = f"   ; -> 0x{t & 0xffffffff:05x} [{tag}]"

    if m in ('ldr', 'ldr.w'):
        for op in ins.operands:
            if op.type == ARM_OP_MEM and op.mem.base == ARM_REG_PC:
                la = ((ins.address + 4) & ~3) + op.mem.disp
                if 0 <= la + 4 <= len(data):
                    w = struct.unpack('<I', data[la:la+4])[0]
                    a = ascii_at(w)
                    note = f"   ; [lit 0x{la:05x}] = 0x{w:08x}"
                    if a:
                        note += f"  ascii->{a!r}"

    print(f"  0x{ins.address:05x}: {ins.bytes.hex():<10s} {m} {ins.op_str}{note}")
    count += 1

    # decode table-branch
    if m == 'tbb':
        tbl = ins.address + ins.size
        print(f"      -- tbb jump table @0x{tbl:05x} (byte offsets, "
              f"target = tbl + 2*entry) --")
        prev = -1
        for k in range(0, 64):
            e = data[tbl + k]
            if e == 0:
                # heuristic table end: a 0 after we've seen entries and
                # next bytes look like code, stop
                if k > 0 and prev != -1:
                    pass
            tgt = tbl + 2 * e
            flag = 'PROLOGUE' if is_prologue(tgt) else ''
            print(f"        case {k:2d}: e=0x{e:02x} -> 0x{tgt:05x} {flag}")
            if k >= 18 and e == 0:
                break
            prev = e
        addr = ins.address + ins.size  # continue after; table is data
        # skip an estimated table length (round up to even); user reads cases
        addr += 20
        continue
    if m == 'tbh':
        tbl = ins.address + ins.size
        print(f"      -- tbh jump table @0x{tbl:05x} (halfword) --")
        for k in range(0, 48):
            e = struct.unpack('<H', data[tbl+2*k:tbl+2*k+2])[0]
            tgt = tbl + 2 * e
            flag = 'PROLOGUE' if 0 <= tgt < len(data) and is_prologue(tgt) else ''
            print(f"        case {k:2d}: e=0x{e:04x} -> 0x{tgt:05x} {flag}")
            if k >= 16 and e == 0:
                break
        addr = ins.address + ins.size + 40
        continue

    addr += ins.size
    if m in ('pop',) and 'pc' in ins.op_str:
        print("  [epilogue: pop {..,pc}]")
        break
    if m == 'bx' and 'lr' in ins.op_str:
        print("  [epilogue: bx lr]")
        break
