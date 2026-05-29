"""
The DFU chunk header length was GUESSED as 8 (args[0:5]=size/tag/page/off,
args[5:8]=CRC, data=args[8:]). Contiguity only proved args[0:5]. Find the
TRUE data offset D empirically.

For each candidate D (0..12) and each chunk-data length DLEN (csz, csz read
from args[0:2]), rebuild region 03 (flash base 0x0000 -> Cortex-M vector
table at image start) by concatenating args[D:D+DLEN] in capture order, and
score:
  - word[0] plausible initial SP  (0x20000000..0x20040000, common for these MCUs)
  - word[1] plausible reset vector (odd=Thumb, points into a sane flash range)
  - prologue density /KB (real firmware ~5-30/KB)
  - longest clean capstone decode run
Highest combined score => true layout. Then dump the recovered vector
table + first function for region 03 and write all three regions.
"""
import struct, collections
from scapy.all import rdpcap
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

PCAP = r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

pkts = rdpcap(PCAP)
# region_tag -> list of (full_args_bytes, csz) in capture order
regions = collections.OrderedDict()
for p in pkts:
    raw = bytes(p)
    if len(raw) < 27 + 8 + 90:
        continue
    so = None
    for off in range(20, min(40, len(raw) - 8)):
        if raw[off] == 0x21 and raw[off+1] == 0x09:
            so = off; break
    if so is None:
        continue
    if struct.unpack('<H', raw[so+6:so+8])[0] != 90:
        continue
    rz = raw[so+8:so+8+90]
    if rz[6] != 0x10 or rz[7] != 0x02:
        continue
    args = rz[8:8+80]
    csz = struct.unpack('<H', args[0:2])[0]
    regions.setdefault(args[2], []).append((args, csz))

PRO = {b'\x80\xb5', b'\x90\xb5', b'\xb0\xb5', b'\x10\xb5', b'\x70\xb5',
       b'\xf0\xb5', b'\xf7\xb5', b'\xf8\xb5', b'\x30\xb5', b'\x00\xb5',
       b'\x08\xb5', b'\x38\xb5', b'\x2d\xe9'}

def build(lst, D):
    return b''.join(a[D:D+csz] for a, csz in lst)

def pro_density(b):
    n = sum(1 for o in range(0, len(b) - 3, 2) if b[o:o+2] in PRO)
    return n * 1024 / max(1, len(b))

def max_run(b, samples=300):
    best = 0
    step = max(2, (len(b) // samples) & ~1)
    for o in range(0, len(b) - 128, step):
        c = sum(1 for _ in md.disasm(b[o:o+128], o))
        best = max(best, c)
    return best

r03 = regions[0x03]
print(f"region 03: {len(r03)} chunks  (flash base 0x0000 => vector table)")
print(f"{'D':>2} {'SP':>10} {'RST':>10} {'Thmb':>4} {'pro/KB':>7} {'maxrun':>6}  verdict")
best = None
for D in range(0, 13):
    img = build(r03, D)
    if len(img) < 8:
        continue
    sp, rst = struct.unpack('<II', img[0:8])
    thumb = rst & 1
    sp_ok = 0x20000000 <= sp <= 0x20040000
    rst_ok = thumb and (0x00000000 <= (rst & ~1) <= 0x00100000 or
                        0x08000000 <= (rst & ~1) <= 0x08100000)
    pd = pro_density(img)
    mr = max_run(img)
    score = (3 if sp_ok else 0) + (3 if rst_ok else 0) + pd / 2 + mr / 8
    v = []
    if sp_ok: v.append("SP-OK")
    if rst_ok: v.append("RST-OK")
    if pd > 4: v.append("CODE-DENSITY")
    print(f"{D:2d} 0x{sp:08x} 0x{rst:08x} {thumb:>4} {pd:7.1f} {mr:6d}  "
          f"{' '.join(v) or '-'}  (s={score:.1f})")
    if best is None or score > best[0]:
        best = (score, D, img, sp, rst)

score, D, img, sp, rst = best
print(f"\n>>> best data offset D={D}  SP=0x{sp:08x} RST=0x{rst:08x}")
print("    vector table (first 16 words):")
for i in range(16):
    w = struct.unpack('<I', img[i*4:i*4+4])[0]
    tag = {0: 'SP', 1: 'Reset', 2: 'NMI', 3: 'HardFault', 11: 'SVC',
           14: 'PendSV', 15: 'SysTick'}.get(i, f'IRQ{i-16}' if i >= 16 else '')
    print(f"      [{i:2d}] 0x{w:08x}  {tag}")

# disassemble the reset handler (mask thumb bit, it's a file offset since base=0)
rh = rst & ~1
print(f"\n    Reset handler @ 0x{rh:06x}:")
for n, ins in enumerate(md.disasm(img[rh:rh+96], rh)):
    print(f"      0x{ins.address:06x}: {ins.bytes.hex():10s} {ins.mnemonic} {ins.op_str}")
    if n >= 23:
        break

if D != 8:
    print(f"\n[!] extract_regions.py used D=8; TRUE D={D}. Rewriting regions.")
    for tag in (0x02, 0x03, 0x04):
        out = build(regions[tag], D)
        path = rf'L:\PROJECTS\razer-joro\captures\joro_region_{tag:02x}_FIXED.bin'
        open(path, 'wb').write(out)
        print(f"    wrote {path} ({len(out)} B, pro {pro_density(out):.1f}/KB)")
else:
    print("\n[ok] D=8 confirmed; existing extraction was correct.")
