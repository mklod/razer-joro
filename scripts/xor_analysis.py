"""
Try to recover a simple XOR key (length 1..32) from one of our captured
firmware regions by looking for the prevalence of common ARM Thumb-2
patterns after XORing with each candidate key byte.
"""
import struct, collections

REGIONS = [
    r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin',
    r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin',
    r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin',
]

# Distinguishing patterns of ARM Thumb-2 code
# - 'bx lr'  = 70 47    (very common at function end)
# - 'mov r0, #imm; bx lr' (return constant) = ?? 20 70 47  -> ends with 70 47
# - push {r4-r7,lr} = F0 B5
# - push {r7, lr}   = 80 B5
# - 'nop'  (16-bit)  = 00 BF
# - branch always (uncond) is 0xE7XX
# - 0xff bytes in flash padding (post-erase)
THUMB_PATTERNS_2B = [b'\x70\x47', b'\x80\xB5', b'\xF0\xB5', b'\x10\xB5',
                    b'\x00\xBF', b'\x00\x00', b'\xFF\xFF']

def score(data: bytes) -> int:
    """Count how many 2-byte aligned positions match a Thumb-2 prologue/return."""
    n = 0
    for off in range(0, len(data) - 1, 2):
        s = data[off:off+2]
        if s in THUMB_PATTERNS_2B:
            n += 1
    return n

def try_xor_key(ct: bytes, key: bytes) -> bytes:
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(ct))

for path in REGIONS:
    print(f"\n=== {path.split(chr(92))[-1]} ===")
    data = open(path, 'rb').read()
    base_score = score(data)
    print(f"  raw score (no XOR): {base_score} hits in {len(data)} bytes")

    # Single-byte XOR
    print(f"  Best single-byte XOR keys:")
    results = []
    for key in range(256):
        xored = try_xor_key(data, bytes([key]))
        s = score(xored)
        results.append((s, key))
    results.sort(reverse=True)
    for s, k in results[:5]:
        print(f"    key=0x{k:02x}  score={s}")

    # 2-byte XOR — too slow for full scan but try a subset
    # Actually 2-byte XOR has 64K possibilities. Skip.

    # 4-byte XOR — search for keys based on the assumption that XOR with
    # repeating key turns 0xff (erased flash) into key bytes. The first chunk
    # of an encrypted region might have encrypted-flash-erase value at a
    # known position. Find the most common 4-byte sequence in the file —
    # that's likely XOR(0xff*4, key) = key XOR 0xffffffff.
    quads = collections.Counter()
    for off in range(0, len(data) - 3, 4):
        quads[data[off:off+4]] += 1
    print(f"  Most common 4-byte sequences (LE u32):")
    for q, c in quads.most_common(5):
        keyguess = bytes(b ^ 0xff for b in q)
        print(f"    {q.hex():16s}  count={c:5d}  -> XOR(FF^4)={keyguess.hex()}")
