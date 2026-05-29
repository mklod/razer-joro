"""
Rigorous cryptanalysis of the captured Joro firmware ciphertext.

Checks (in order of how fatal a positive result is):
 1. ECB detection: repeated ciphertext blocks at sizes 4/8/16/32.
    Firmware has large 0xFF/0x00 plaintext runs -> under ECB those become
    identical repeating ciphertext blocks. Lots of repeats == ECB.
 2. Fixed/repeating-keystream (stream or CTR with reused keystream):
    treat the image as columns of period P (try P = 16, 32, 64=DFU chunk,
    and others). If one keystream is reused per period, then
    column[i] = plaintext_i XOR keystream[i]. Firmware plaintext is
    dominated by 0x00 (sparse Thumb-2) and 0xFF (erased flash padding),
    so the MODE of each column ~= keystream[i] (XOR 0x00) or
    keystream[i]^0xFF. Recover candidate keystream from column modes,
    decrypt, and score the result for ARM Thumb-2 plausibility.
 3. Chunk-pair XOR: XOR pairs of 64-byte DFU chunks. If keystream is
    reused per chunk, ciphA^ciphB = plainA^plainB (keystream cancels);
    padding-heavy chunks then show long 0x00 runs -> proves reuse.
 4. Entropy-vs-offset and global stats.

Scoring a candidate plaintext = density of Thumb-2 prologues / bx lr /
literal-pool / nop patterns (real code) vs random.
"""
import sys, struct, collections, math, re

REGIONS = [
    r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin',
    r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin',
    r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin',
]

THUMB2 = [b'\x70\x47', b'\x80\xB5', b'\x90\xB5', b'\x10\xB5', b'\xF0\xB5',
          b'\xF7\xB5', b'\xF8\xB5', b'\x00\xBF', b'\x2d\xe9', b'\xbd\xe8',
          b'\x00\x00', b'\xff\xff']

def code_score(b: bytes) -> int:
    n = 0
    for off in range(0, len(b) - 1, 2):
        if b[off:off+2] in THUMB2:
            n += 1
    return n

def entropy(b: bytes) -> float:
    if not b: return 0.0
    f = collections.Counter(b)
    L = len(b)
    return -sum((c/L) * math.log2(c/L) for c in f.values())

def ecb_check(data: bytes):
    print("  -- ECB / repeated-block --")
    for bs in (4, 8, 16, 32):
        blocks = [data[i:i+bs] for i in range(0, len(data) - bs + 1, bs)]
        total = len(blocks)
        ctr = collections.Counter(blocks)
        distinct = len(ctr)
        top, topn = ctr.most_common(1)[0]
        # 'duplicate rate' excluding the single most common (which may be
        # encrypt(padding)); high secondary dup rate is the real ECB tell
        dup = total - distinct
        print(f"    bs={bs:2d}: blocks={total:6d} distinct={distinct:6d} "
              f"dup={dup:6d} ({100*dup/total:5.1f}%)  top×{topn} = {top.hex()}")

def keystream_attack(data: bytes, period: int):
    """Assume repeating keystream of `period`. keystream[i] estimated as the
    column mode (works if plaintext column is dominated by 0x00 or 0xFF)."""
    cols = [collections.Counter() for _ in range(period)]
    for i, byte in enumerate(data):
        cols[i % period][byte] += 1
    # Two hypotheses for dominant plaintext byte: 0x00 and 0xFF
    for assumed_pt, label in ((0x00, 'pt=00'), (0xFF, 'pt=FF')):
        ks = bytes((c.most_common(1)[0][0] ^ assumed_pt) if c else 0 for c in cols)
        dec = bytes(b ^ ks[i % period] for i, b in enumerate(data))
        sc = code_score(dec)
        yield label, period, ks, dec, sc

def chunk_pair_xor(data: bytes, csz=64):
    """If keystream reused per `csz` chunk, ciphA^ciphB = plainA^plainB.
    Padding-heavy chunks then have long 0x00 runs. Report best pair."""
    chunks = [data[i:i+csz] for i in range(0, len(data) - csz + 1, csz)]
    best = (0, -1, -1)
    for a in range(min(len(chunks), 200)):
        for b in range(a+1, min(len(chunks), 200)):
            x = bytes(p ^ q for p, q in zip(chunks[a], chunks[b]))
            zeros = x.count(0)
            if zeros > best[0]:
                best = (zeros, a, b)
    return best, len(chunks)

for path in REGIONS:
    name = path.split('\\')[-1]
    data = open(path, 'rb').read()
    print(f"\n{'='*72}\n{name}  ({len(data)} bytes, entropy {entropy(data):.3f})")
    ecb_check(data)

    print("  -- repeating-keystream attack (column-mode) --")
    best = None
    for period in (8, 16, 24, 32, 48, 64, 80, 90, 128, 256):
        for label, P, ks, dec, sc in keystream_attack(data, period):
            if best is None or sc > best[4]:
                best = (label, P, ks, dec, sc)
            if sc > len(data) * 0.02:  # notably code-like
                print(f"    *** period={P:3d} {label}: code_score={sc} "
                      f"(raw={code_score(data)}) ks[:16]={ks[:16].hex()}")
    if best:
        label, P, ks, dec, sc = best
        raw = code_score(data)
        flag = "  <-- PROMISING" if sc > raw * 3 and sc > 200 else ""
        print(f"    best: period={P} {label} score={sc} (raw {raw}){flag}")
        if flag:
            out = path.replace('.bin', f'.dec_p{P}_{label}.bin')
            open(out, 'wb').write(dec)
            print(f"    wrote candidate plaintext: {out}")

    (zeros, a, b), nch = chunk_pair_xor(data)
    print(f"  -- chunk(64) pair XOR --  best pair {a},{b}: {zeros}/64 zero "
          f"bytes (of {nch} chunks) — high == reused per-chunk keystream")
