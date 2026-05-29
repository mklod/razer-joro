"""
Parse the 'Razer Customer Firmware Updater' archive overlay.
Format observed:
  31-byte signature: 'Razer Customer Firmware Updater'
  u32 LE: filename length
  N bytes: filename (no null terminator)
  u32 LE: file size
  N bytes: file content
  ... repeat
"""
import struct, os, re

PATH = r'L:\PROJECTS\razer-joro\captures\fw_updater_overlay.bin'
OUT_DIR = r'L:\PROJECTS\razer-joro\captures\fwu_extract'
os.makedirs(OUT_DIR, exist_ok=True)

data = open(PATH, 'rb').read()
print(f"overlay: {len(data)} bytes ({len(data)/1024/1024:.1f} MB)")

# Find every occurrence of the signature
sig = b'Razer Customer Firmware Updater'
sig_offsets = [m.start() for m in re.finditer(re.escape(sig), data)]
print(f"\nSignature '{sig.decode()}' found at offsets: {sig_offsets}")

# Tentatively parse using the first signature as the header.
off = sig_offsets[0] + len(sig)

entries = []
while off < len(data):
    if off + 4 > len(data): break
    name_len = struct.unpack('<I', data[off:off+4])[0]
    if name_len == 0 or name_len > 256:
        break
    off += 4
    if off + name_len > len(data): break
    name = data[off:off+name_len].decode('latin1', errors='replace')
    off += name_len
    if off + 4 > len(data): break
    size = struct.unpack('<I', data[off:off+4])[0]
    off += 4
    if off + size > len(data) or size < 0:
        print(f"  Truncated at entry '{name}' size={size}")
        break
    payload = data[off:off + size]
    off += size
    entries.append((name, size, payload))

print(f"\nParsed {len(entries)} entries:")
for (name, size, payload) in entries:
    out = os.path.join(OUT_DIR, os.path.basename(name) or 'noname')
    # avoid clobbering by appending name index
    base, ext = os.path.splitext(out)
    n = 0
    while os.path.exists(out):
        n += 1
        out = f"{base}_{n}{ext}"
    with open(out, 'wb') as f:
        f.write(payload)
    sig8 = payload[:16].hex() if payload else ''
    print(f"  {name:50s} {size:>10d} bytes  head=[{sig8}]")

# Total parsed bytes vs overlay size
total_parsed = sum(s for _, s, _ in entries)
header_bytes = sum(4 + len(n) + 4 for n, _, _ in entries) + len(sig)
print(f"\nTotal: parsed={total_parsed}  headers={header_bytes}  overlay={len(data)}")
print(f"Trailing unparsed: {len(data) - off} bytes (offset 0x{off:x})")
