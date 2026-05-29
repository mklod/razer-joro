"""
Disassemble each region at multiple candidate offsets to find where the
real code starts. Report a 'sanity score' based on:
  - prologue density per 1KB
  - frequency of common Thumb-2 ops (push/pop/bl/bx/mov/ldr)
  - low frequency of nonsense instructions
"""
import sys, struct, re
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

REGIONS = [
    (r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin', 0x7000),
    (r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin', 0x0000),
    (r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin', 0x0000),
]

PROLOGUES = [b'\x80\xB5', b'\x90\xB5', b'\x10\xB5', b'\xF0\xB5', b'\xF7\xB5',
             b'\xF8\xB5', b'\x70\xB5', b'\x30\xB5', b'\x00\xB5', b'\xB0\xB5']

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

for path, base in REGIONS:
    print(f"\n{'='*72}")
    print(f"REGION: {path}")
    print(f"  declared base in protocol = 0x{base:04x}")
    fw = open(path, 'rb').read()
    print(f"  size = {len(fw)} bytes")

    # Prologue density
    prologue_count = 0
    for pat in PROLOGUES:
        prologue_count += sum(1 for m in re.finditer(re.escape(pat), fw) if m.start() % 2 == 0)
    print(f"  Total prologue matches: {prologue_count}  (density {prologue_count*1024/len(fw):.1f}/KB)")

    # First u32 + second u32 (vector table candidate)
    if len(fw) >= 16:
        for i in range(min(8, len(fw)//4)):
            v = struct.unpack('<I', fw[i*4:(i+1)*4])[0]
            print(f"  vec[{i}] = 0x{v:08x}  Thumb={v&1}")

    # Disasm first 30 instructions from offset 0
    print(f"\n  Disasm @ offset 0:")
    for ins in list(md.disasm(fw[:200], base))[:15]:
        print(f"    0x{ins.address:08x}: {ins.bytes.hex():<8} {ins.mnemonic} {ins.op_str}")

    # If region has internal pointers like 0x027... try treating those as
    # offsets into THIS region with various flash bases.
    if len(fw) >= 8:
        v0 = struct.unpack('<I', fw[0:4])[0]
        v1 = struct.unpack('<I', fw[4:8])[0]
        # Hypothesize: if v1 is the reset address and points within flash,
        # base = v1's high half.
        for guess_base in [v1 & 0xFFFF0000, v1 & 0xFFF00000, v1 & 0xFFFC0000]:
            inside = guess_base <= v1 < guess_base + len(fw)
            if inside:
                off = v1 - guess_base
                print(f"\n  Try load_base=0x{guess_base:08x} -> reset at file off 0x{off:x}")
                if 0 <= off < len(fw) - 60:
                    for ins in list(md.disasm(fw[off:off+60], v1 & ~1))[:10]:
                        print(f"    0x{ins.address:08x}: {ins.bytes.hex():<8} {ins.mnemonic} {ins.op_str}")
                    break
