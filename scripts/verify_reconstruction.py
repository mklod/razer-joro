"""
Verify the DFU chunk -> firmware-image reconstruction is sound.

Hypothesis #2: extract_regions.py's address decode
    addr = (args[3] << 8) | args[4]
is wrong, so chunks collide on `addr` and `regions[tag][addr]=data`
silently overwrites -> reconstructed image is shredded, not encrypted.

This script does NOT assume the decode. It dumps the raw per-chunk
header bytes args[0:9] in capture order and reports, per region:
  - chunk count vs DISTINCT decoded addr (collision => reconstruction lost data)
  - whether the capture-order data, simply concatenated, disassembles
    (if firmware is plaintext and chunks arrive in order, raw concat IS
    the image regardless of any address field)
  - the actual progression of args[2..4] so the TRUE address encoding
    is visible.
"""
import struct, glob, os, collections
from scapy.all import rdpcap
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

CAPDIR = r'L:\PROJECTS\razer-joro\captures'
# only fw_update_u1.pcap has real data (2MB); the rest are empty stubs
PCAP = r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
print(f"using: {PCAP}\n")
pkts = rdpcap(PCAP)
print(f"{len(pkts)} packets")

md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

# region_tag -> list of (chunk_idx, args0_8, data) in CAPTURE ORDER
regions = collections.OrderedDict()
seq = 0
for i, p in enumerate(pkts):
    raw = bytes(p)
    if len(raw) < 27 + 8 + 90:
        continue
    so = None
    for off in range(20, min(40, len(raw) - 8)):
        if raw[off] == 0x21 and raw[off+1] == 0x09:
            so = off; break
    if so is None:
        continue
    wLength, = struct.unpack('<H', raw[so+6:so+8])
    if wLength != 90:
        continue
    rz = raw[so+8:so+8+90]
    if rz[6] != 0x10 or rz[7] != 0x02:
        continue
    args = rz[8:8+80]
    csz = struct.unpack('<H', args[0:2])[0]
    tag = args[2]
    data = bytes(args[8:8+csz])
    regions.setdefault(tag, []).append((seq, args[0:9], data))
    seq += 1

for tag, lst in regions.items():
    print(f"\n{'='*68}\nregion 0x{tag:02x}: {len(lst)} chunks (capture order)")
    # collision test under the OLD decode
    old_addrs = []
    for _, a, _ in lst:
        old_addrs.append((a[3] << 8) | a[4])
    distinct = len(set(old_addrs))
    print(f"  OLD decode addr=(args[3]<<8)|args[4]: {distinct} distinct "
          f"of {len(lst)} -> {'COLLISIONS, data lost!' if distinct < len(lst) else 'no collision'}")

    # show first 16 + last 4 raw headers
    print("  idx  args[0:9]            csz  tag a3 a4  oldaddr")
    for k, (s, a, d) in enumerate(lst):
        if k < 16 or k >= len(lst) - 4:
            cs = struct.unpack('<H', a[0:2])[0]
            print(f"  {k:4d} {a.hex()}  {cs:3d}  {a[2]:02x} {a[3]:02x} {a[4]:02x}"
                  f"  0x{(a[3]<<8)|a[4]:04x}")
        elif k == 16:
            print("   ...")

    # KEY TEST: raw concat in capture order, disassemble from off 0
    blob = b''.join(d for _, _, d in lst)
    # prologue density on raw concat
    proheads = (b'\x80\xb5', b'\x90\xb5', b'\xb0\xb5', b'\x10\xb5',
                b'\x70\xb5', b'\xf0\xb5', b'\xf7\xb5', b'\xf8\xb5',
                b'\x30\xb5', b'\x00\xb5', b'\x08\xb5', b'\x38\xb5')
    npro = sum(1 for o in range(0, len(blob)-3, 2)
               if blob[o:o+2] in proheads or blob[o:o+2] == b'\x2d\xe9')
    print(f"  RAW-CONCAT {len(blob)} B: prologue density "
          f"{npro*1024/max(1,len(blob)):.1f}/KB "
          f"(plaintext code ~5-30/KB; noise ~<1/KB)")
    # try a few alignments / known Cortex-M load bases for a vector table
    sp, rst = struct.unpack('<II', blob[0:8]) if len(blob) >= 8 else (0, 0)
    print(f"  RAW-CONCAT vec: SP=0x{sp:08x} RST=0x{rst:08x} "
          f"Thumb={rst & 1} "
          f"({'plausible Cortex-M' if 0x20000000 <= sp <= 0x20040000 and rst & 1 else 'not a vector table here'})")
    # disassemble first 12 ins of raw concat for eyeball
    print("  RAW-CONCAT disasm @0:")
    for n, ins in enumerate(md.disasm(blob[:64], 0)):
        print(f"    {ins.address:04x}: {ins.bytes.hex():10s} {ins.mnemonic} {ins.op_str}")
        if n >= 11:
            break
