"""
Scan the Joro firmware blob for likely ARM Thumb-2 code regions by looking
for common function-prologue patterns. The blob may have a header /
metadata before the actual code.
"""
import struct
import re

FW = open(r'L:\PROJECTS\razer-joro\captures\joro_firmware.bin', 'rb').read()
print(f"firmware: {len(FW)} bytes")

# Common Thumb-2 function prologue encodings (little-endian 16-bit):
# - "push {r7, lr}"     = 0xB580
# - "push {r4, r7, lr}" = 0xB590
# - "push {r4, lr}"     = 0xB510
# - "push {r7}"         = 0xB480
# - "push {r4-r7}"      = 0xB4F0
# - "push {r4-r7, lr}"  = 0xB5F0
# - "sub sp, #0x..."    = 0xB0__   (low byte = imm)
PROLOGUES = [
    (b'\x80\xB5', 'push {r7,lr}'),
    (b'\x90\xB5', 'push {r4,r7,lr}'),
    (b'\x10\xB5', 'push {r4,lr}'),
    (b'\xF0\xB5', 'push {r4-r7,lr}'),
    (b'\xF7\xB5', 'push {r0-r2,r4-r7,lr}'),
    (b'\xF8\xB5', 'push {r3,r4-r7,lr}'),
    (b'\x70\xB5', 'push {r4-r6,lr}'),
    (b'\x30\xB5', 'push {r4,r5,lr}'),
    (b'\x00\xB5', 'push {lr}'),
    (b'\xB0\xB5', 'push {r4,r5,r7,lr}'),
]

# Bucket the file in 1KB windows and count prologue-pattern density.
WIN = 1024
counts = [0] * ((len(FW) + WIN - 1) // WIN)
for pat, _ in PROLOGUES:
    for m in re.finditer(re.escape(pat), FW):
        # only count even offsets (Thumb-2 instructions are 2-byte aligned)
        if m.start() % 2 == 0:
            counts[m.start() // WIN] += 1

# Print density per 1KB window
print("\n1KB window prologue counts (* = high density, looks like code):")
mx = max(counts) or 1
for i, c in enumerate(counts):
    bar = '*' * min(40, int(c * 40 / mx))
    if c > 0:
        print(f"  off=0x{i*WIN:05x}  count={c:3d} {bar}")

# Find first prologue
print("\nFirst 10 prologue matches (in order):")
hits = []
for pat, name in PROLOGUES:
    for m in re.finditer(re.escape(pat), FW):
        if m.start() % 2 == 0:
            hits.append((m.start(), name))
hits.sort()
for off, name in hits[:10]:
    print(f"  off=0x{off:06x}: {name}")
