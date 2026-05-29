"""
Re-extract Joro firmware respecting chunk addressing. Each cmd=0x02 packet
header is 9 bytes (VERIFIED 2026-05-18 by find_data_offset.py — the data
offset is D=9, NOT 8; an earlier off-by-one here made the whole image look
"encrypted" when it is plain Thumb-2):
  args[0..1]  LE u16 = chunk size (bytes of firmware data, typ. 0x40)
  args[2]     u8     = region tag (0x02 / 0x03 / 0x04 observed)
  args[3]     u8     = page  (high byte of region-relative address)
  args[4]     u8     = offset within page (low byte; cycles 00/40/80/c0)
  args[5..8]  4-byte CRC
  args[9..]   firmware data (chunk_size bytes)

Total flash address (within the *region*) = (args[3] << 8) | args[4].

This script groups chunks by region tag, sparse-loads each into a per-
region image padded with 0xFF, and writes them as separate files. Then
each can be tried as a candidate Cortex-M application image.
"""
import struct
from collections import defaultdict
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
OUT_DIR = r'L:\PROJECTS\razer-joro\captures'

pkts = rdpcap(PCAP)
print(f"Loaded {len(pkts)} packets")

# region_tag -> {addr: bytes}
regions = defaultdict(dict)
chunk_log = []
for i, p in enumerate(pkts):
    raw = bytes(p)
    if len(raw) < 27 + 8 + 90:
        continue
    setup_off = None
    for off in range(20, min(40, len(raw) - 8)):
        if raw[off] == 0x21 and raw[off+1] == 0x09:
            setup_off = off
            break
    if setup_off is None:
        continue
    wLength, = struct.unpack('<H', raw[setup_off+6:setup_off+8])
    if wLength != 90:
        continue
    razer = raw[setup_off+8:setup_off+8+90]
    if razer[6] != 0x10 or razer[7] != 0x02:
        continue
    args = razer[8:8+80]
    chunk_size = struct.unpack('<H', args[0:2])[0]
    region_tag = args[2]
    page_msb = args[3]
    offset_within_page = args[4]
    addr = (page_msb << 8) | offset_within_page
    data = bytes(args[9:9 + chunk_size])   # D=9: 5-byte hdr + 4-byte CRC
    regions[region_tag][addr] = data
    chunk_log.append((i+1, region_tag, page_msb, offset_within_page, chunk_size))

print(f"\nRegion summary:")
for tag in sorted(regions):
    addrs = sorted(regions[tag].keys())
    total_bytes = sum(len(d) for d in regions[tag].values())
    print(f"  region 0x{tag:02x}: {len(regions[tag])} chunks, "
          f"addr 0x{addrs[0]:04x}..0x{addrs[-1]:04x}, "
          f"total {total_bytes} bytes ({total_bytes/1024:.1f} KB)")

# Build each region's image: sparse-fill with 0xFF, copy chunks at their
# declared addresses. The image base = min addr; size = max addr +
# chunk_size_at_max - min addr.
for tag, chunks in sorted(regions.items()):
    addrs = sorted(chunks.keys())
    base = addrs[0]
    last_addr = addrs[-1]
    last_size = len(chunks[last_addr])
    size = (last_addr + last_size) - base
    img = bytearray(b'\xFF' * size)
    for a, d in chunks.items():
        img[a - base : a - base + len(d)] = d
    out = f"{OUT_DIR}\\joro_region_{tag:02x}_at_0x{base:04x}.bin"
    with open(out, 'wb') as f:
        f.write(img)
    print(f"  region 0x{tag:02x}: wrote {out} ({size} bytes, base 0x{base:04x})")

    # Quick vector-table check on this region
    if size >= 16:
        sp = struct.unpack('<I', bytes(img[0:4]))[0]
        reset = struct.unpack('<I', bytes(img[4:8]))[0]
        thumb = reset & 1
        print(f"      first u32 (potential SP)    = 0x{sp:08x}")
        print(f"      second u32 (potential reset) = 0x{reset:08x}  Thumb={thumb}")
