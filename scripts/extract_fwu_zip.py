"""Extract files from the malformed ZIP by walking PK\x03\x04 headers."""
import struct, os, zlib

SRC = r'L:\PROJECTS\razer-joro\captures\fwu_extract\CustomerFWU2Point5.exe.zip'
OUT = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents'
os.makedirs(OUT, exist_ok=True)

data = open(SRC, 'rb').read()
off = 0
i = 0
while True:
    idx = data.find(b'PK\x03\x04', off)
    if idx == -1: break
    hdr = data[idx:idx+30]
    if len(hdr) < 30: break
    flag, method, _, _, crc, csize, usize, fname_len, extra_len = struct.unpack('<HHHHIIIHH', hdr[6:30])
    fname = data[idx+30 : idx+30+fname_len].decode('utf-8', errors='replace')
    body = data[idx+30+fname_len+extra_len : idx+30+fname_len+extra_len+csize]
    if method == 8:
        try:
            decompressed = zlib.decompress(body, -15)  # raw deflate
        except Exception as e:
            print(f"  [{i}] {fname}: decompress error: {e}")
            decompressed = b''
    elif method == 0:
        decompressed = body
    else:
        print(f"  [{i}] {fname}: unsupported method {method}")
        decompressed = b''
    safe = fname.replace('/', '_').replace('\\', '_')
    out_path = os.path.join(OUT, safe)
    open(out_path, 'wb').write(decompressed)
    print(f"  [{i:2d}] {fname:50s} csz={csize:>10d}  usz={len(decompressed):>10d}  -> {safe}")
    off = idx + 30 + fname_len + extra_len + csize
    i += 1

print(f"\nExtracted {i} files to {OUT}")
