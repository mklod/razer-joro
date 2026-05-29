"""
Extract Joro firmware from a USBPcap capture of a Razer firmware update
session. Each `class=0x10 cmd=0x02` packet carries 72 bytes of firmware
data in args[8..79] following an 8-byte chunk header (flash address + CRC).

Output: <out>.bin (raw concatenated firmware), <out>_chunks.txt (per-chunk
metadata: frame, header, address-if-decodable, CRC).
"""
import sys, struct
from scapy.all import rdpcap

PCAP = sys.argv[1] if len(sys.argv) > 1 else 'L:/PROJECTS/razer-joro/captures/fw_update_u1.pcap'
OUT_BIN = sys.argv[2] if len(sys.argv) > 2 else 'L:/PROJECTS/razer-joro/captures/joro_firmware.bin'
OUT_META = OUT_BIN.rsplit('.', 1)[0] + '_chunks.txt'

pkts = rdpcap(PCAP)
print(f"Loaded {len(pkts)} packets")

chunks = []
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
    if len(razer) != 90:
        continue
    cclass, ccmd = razer[6], razer[7]
    if cclass != 0x10 or ccmd != 0x02:
        continue
    args = razer[8:8+80]  # full 80-byte args
    header = args[0:8]
    # args[0..1] LE u16 = actual flash chunk size (typically 0x40=64,
    # last chunk is short: 0x34=52). Data follows starting at args[8].
    chunk_size = struct.unpack('<H', header[0:2])[0]
    data = args[8:8 + chunk_size]
    if len(data) != chunk_size:
        print(f"  WARN frame {i+1}: header size 0x{chunk_size:x} but only {len(data)} bytes available")
    chunks.append((i+1, header, data))

print(f"Found {len(chunks)} firmware chunks (cmd=0x10/0x02)")

# Write metadata
with open(OUT_META, 'w') as f:
    for (frame, hdr, data) in chunks:
        hex_hdr = ' '.join(f'{b:02x}' for b in hdr)
        addr_le = struct.unpack('<I', hdr[0:4])[0]
        crc_le = struct.unpack('<I', hdr[4:8])[0]
        f.write(f"frame={frame}  header=[{hex_hdr}]  addr_le=0x{addr_le:08x}  crc_le=0x{crc_le:08x}\n")
print(f"Wrote per-chunk metadata: {OUT_META}")

# Concatenate firmware
fw = b''.join(data for (_, _, data) in chunks)
with open(OUT_BIN, 'wb') as f:
    f.write(fw)
print(f"Wrote firmware blob: {OUT_BIN}  ({len(fw)} bytes = {len(fw)/1024:.1f} KB)")

# Quick sanity check — ARM Cortex-M vector table starts with initial SP at offset 0
if len(fw) >= 8:
    sp = struct.unpack('<I', fw[0:4])[0]
    reset = struct.unpack('<I', fw[4:8])[0]
    print(f"\nFirst 8 bytes (potential ARM Cortex-M vector table):")
    print(f"  initial SP    = 0x{sp:08x}  (expect SRAM, e.g. 0x20020000-ish)")
    print(f"  reset handler = 0x{reset:08x}  (expect flash address)")
