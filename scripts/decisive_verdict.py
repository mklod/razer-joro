"""
Decisive: is the captured firmware COMPRESSED (software-recoverable) or
ENCRYPTED (needs the on-chip key -> hardware)?

Discriminators:
  A. Magic bytes at region start AND at every 64B DFU-chunk boundary
     (compressors leave headers; Razer/Nordic/ST/LZMA/zlib/gzip/lz4).
  B. Windowed entropy across offset. Encryption -> flat ~7.99 everywhere.
     LZ compression -> ~6.0-7.0 with a low-entropy header and visible
     structure/seams. We already see whole-region 6.6-6.7 (NOT 7.99).
  C. Byte-value chi-square vs uniform. AES/stream output is
     statistically uniform (chi-sq small relative to dof). Compressed
     binary is close-ish but skewed; XOR-obfuscated code is very skewed.
  D. Monobit + serial correlation: ciphertext ~ 0 correlation;
     compressed streams show residual byte-pair correlation.
  E. Brute single/short XOR + try standard decompressors on the result
     (zlib raw/deflate, gzip, lzma, bz2, lz4 if available) at offset 0
     and at each chunk boundary.
"""
import collections, math, struct, zlib, lzma, bz2, gzip, io

REGIONS = {
    '02@0x7000': r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin',
    '03@0x0000': r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin',
    '04@0x0000': r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin',
}

MAGICS = {
    b'\x1f\x8b': 'gzip', b'\x78\x01': 'zlib/none', b'\x78\x9c': 'zlib/def',
    b'\x78\xda': 'zlib/best', b'\x5d\x00': 'lzma', b'\xfd7zXZ': 'xz',
    b'BZh': 'bzip2', b'\x04\x22\x4d\x18': 'lz4', b'\x28\xb5\x2f\xfd': 'zstd',
    b'RZ': 'razer?', b'\x52\x5a': 'RZ', b'\xf7\x02': 'nordic-dfu?',
}

def entropy(b):
    if not b: return 0.0
    f = collections.Counter(b); L = len(b)
    return -sum((c/L) * math.log2(c/L) for c in f.values())

def chi_sq_uniform(b):
    f = collections.Counter(b); L = len(b); exp = L / 256
    return sum((f.get(i, 0) - exp) ** 2 / exp for i in range(256))

def serial_corr(b):
    n = len(b) - 1
    if n < 2: return 0.0
    mean = sum(b) / len(b)
    num = sum((b[i] - mean) * (b[i+1] - mean) for i in range(n))
    den = sum((x - mean) ** 2 for x in b)
    return num / den if den else 0.0

def try_decompress(buf, tag):
    res = []
    for name, fn in [
        ('zlib', lambda d: zlib.decompress(d)),
        ('raw-deflate', lambda d: zlib.decompress(d, -15)),
        ('gzip', lambda d: gzip.decompress(d)),
        ('lzma', lambda d: lzma.decompress(d)),
        ('lzma-raw', lambda d: lzma.decompress(
            d, format=lzma.FORMAT_RAW,
            filters=[{'id': lzma.FILTER_LZMA1, 'dict_size': 1 << 16}])),
        ('bz2', lambda d: bz2.decompress(d)),
    ]:
        try:
            out = fn(buf)
            if out and len(out) > 32:
                res.append(f"      {tag}:{name} -> {len(out)} bytes OK "
                           f"(ent {entropy(out[:4096]):.2f})")
        except Exception:
            pass
    return res

for tag, path in REGIONS.items():
    d = open(path, 'rb').read()
    L = len(d)
    print(f"\n{'='*70}\nregion {tag}  {L} B")
    print(f"  whole-region entropy   {entropy(d):.4f}  (AES/stream ~7.99)")
    cs = chi_sq_uniform(d)
    # For truly uniform bytes over L samples, chi-sq ~ 255 (dof). Ratio
    # >> 1 means non-uniform (NOT a good cipher).
    print(f"  chi-sq / 255 (dof)     {cs/255:8.1f}   "
          f"({'~uniform=cipher' if cs/255 < 1.5 else 'NON-uniform -> not strong-cipher'})")
    print(f"  serial correlation     {serial_corr(d):+.4f}  "
          f"(cipher ~0; compressed/obfusc != 0)")

    # A. magic scan
    hits = []
    for mg, nm in MAGICS.items():
        if d.startswith(mg):
            hits.append(f"START={nm}")
    for cb in range(0, min(L, 64 * 60), 64):
        for mg, nm in MAGICS.items():
            if d[cb:cb + len(mg)] == mg:
                hits.append(f"@{cb}({nm})")
    print(f"  compression magic      {hits[:8] if hits else 'none in first 60 chunks/start'}")

    # B. windowed entropy
    win = 2048
    ents = [entropy(d[i:i+win]) for i in range(0, L - win, win)]
    if ents:
        lo, hi = min(ents), max(ents)
        flat = (hi - lo) < 0.25
        print(f"  windowed entropy 2K    min {lo:.2f} max {hi:.2f} "
              f"({'FLAT -> uniform cipher' if flat and lo > 7.5 else 'VARIABLE -> structured (compress/data)'})")

    # E. decompress raw + after single-byte XOR sweep (offset 0 and chunk 1)
    found = []
    for base in (0, 64, 128):
        seg = d[base:base + 8192]
        found += try_decompress(seg, f"raw@{base}")
        for k in range(1, 256):
            xb = bytes(x ^ k for x in seg)
            r = try_decompress(xb, f"xor{k:02x}@{base}")
            if r:
                found += r
    print("  decompress attempts    " + ("\n" + "\n".join(found) if found
          else "none succeeded (raw or single-XOR + zlib/gzip/lzma/bz2)"))
