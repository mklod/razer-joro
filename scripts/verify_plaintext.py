"""
Test whether region_03 is plaintext ARM Thumb-2. Scan for push.w
prologues (2d e9 .. ..) and 16-bit push (b5xx) at even offsets, then
disassemble a window at each candidate and report ones that yield a
coherent function (prologue -> body with calls/branches -> pop/bx lr).
"""
import struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

P = r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin'
data = open(P, 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True

# candidate function starts: 32-bit push.w {..,lr}: bytes 2D E9 .. (4x/4f/41/47/48/43/f8)
# 16-bit push {..,lr}: 80b5 / 90b5 / b0b5 / 10b5 / 70b5 / f0b5 / f7b5 / f8b5 / 30b5 / 00b5
starts = []
for off in range(0, len(data) - 4, 2):
    w = data[off:off+2]
    if w == b'\x2d\xe9':
        starts.append(off)
    elif w in (b'\x80\xb5', b'\x90\xb5', b'\xb0\xb5', b'\x10\xb5', b'\x70\xb5',
               b'\xf0\xb5', b'\xf7\xb5', b'\xf8\xb5', b'\x30\xb5'):
        starts.append(off)
print(f"region_03: {len(data)} bytes, {len(starts)} candidate prologues "
      f"({len(starts)*1024/len(data):.1f}/KB)")

def disas_ok(off, want=24):
    """Disassemble from off; return (n_decoded, has_epilogue, listing)."""
    out = []
    n = 0
    epi = False
    for ins in md.disasm(data[off:off+want*4], off):
        out.append(f"    0x{ins.address:06x}: {ins.bytes.hex():12s} {ins.mnemonic} {ins.op_str}")
        n += 1
        m = ins.mnemonic
        if m in ('bx',) and 'lr' in ins.op_str:
            epi = True; break
        if m == 'pop' and ('pc' in ins.op_str):
            epi = True; break
        if m.startswith('pop') and 'lr' not in ins.op_str and 'pc' in ins.op_str:
            epi = True; break
        if n >= want:
            break
    return n, epi, out

# Show the first 6 candidates' disassembly
shown = 0
for off in starts:
    n, epi, lst = disas_ok(off)
    # Heuristic: a real function decodes many instructions w/o capstone
    # choking and reaches an epilogue.
    if n >= 12:
        print(f"\n-- candidate @ off 0x{off:06x}  (decoded {n}, epilogue={epi}) --")
        print('\n'.join(lst[:20]))
        shown += 1
        if shown >= 6:
            break

# Also: histogram of decoded-instruction run-length from every 2-byte
# offset — plaintext code has long clean runs; random/encrypted chokes fast.
import statistics
runs = []
step = max(2, (len(data)//4000)*2)
for off in range(0, len(data)-64, step):
    c = 0
    for ins in md.disasm(data[off:off+64], off):
        c += 1
    runs.append(c)
print(f"\nDecode-run sanity: mean {statistics.mean(runs):.1f} ins/64B, "
      f"max {max(runs)} (plaintext Thumb-2 ~ 16-32; random/encrypted ~ <12)")
