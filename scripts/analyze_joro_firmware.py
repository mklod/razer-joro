"""
First-pass static analysis of the Joro firmware blob.

Steps:
1. Parse vector table — identify load base, validate as ARM Cortex-M.
2. Find the reset handler — disassemble first ~50 instructions.
3. Find string literals + their cross-references (rough heuristic).
4. Locate Razer Protocol30 handler dispatch by scanning for known cmd
   class bytes (0x10, 0x02, 0x07 etc.) appearing as immediate values
   in nearby instructions.
"""
import struct, re
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

FW = open(r'L:\PROJECTS\razer-joro\captures\joro_firmware.bin', 'rb').read()
print(f"firmware: {len(FW)} bytes ({len(FW)/1024:.1f} KB)")

# ── 1. Parse vector table ──────────────────────────────────────────────────
# ARM Cortex-M layout: u32[0]=initial SP, u32[1]=reset, then NMI/HardFault/...
sp     = struct.unpack('<I', FW[0:4])[0]
reset  = struct.unpack('<I', FW[4:8])[0]
nmi    = struct.unpack('<I', FW[8:12])[0]
hfault = struct.unpack('<I', FW[12:16])[0]
print(f"\nVector table:")
print(f"  [0] initial SP      = 0x{sp:08x}")
print(f"  [1] reset handler   = 0x{reset:08x}  (Thumb LSB={reset&1})")
print(f"  [2] NMI handler     = 0x{nmi:08x}")
print(f"  [3] HardFault       = 0x{hfault:08x}")

# Heuristic: load base = (reset_address >> 16) << 16, but reset has bit0 set
# for Thumb so we mask it. The blob's offset within flash is where we put
# byte 0; the reset handler should resolve to flash_base + offset_in_blob.
# Try the simplest case: load base = floor(reset & ~1, 0x1000).
reset_addr = reset & ~1
load_base_guess = reset_addr & 0xFFFF0000  # round down to 64K
# Actually try the more useful: reset must be inside our 173 KB blob.
# So load_base = reset_addr - (something in [0, len(FW)]).
# We can scan for the most plausible base by checking that all vector
# entries point inside [load_base, load_base + len(FW)].
candidates = []
for guess in [
    reset_addr & 0xFFFFF000,   # 4 K aligned
    reset_addr & 0xFFFF0000,   # 64 K aligned
    reset_addr & 0xFFF80000,   # 512 K aligned
    reset_addr & 0xFFF00000,   # 1 M aligned
]:
    in_range = 0
    total = 0
    for i in range(16):
        v = struct.unpack('<I', FW[i*4:(i+1)*4])[0] & ~1
        if v == 0: continue
        total += 1
        if guess <= v < guess + len(FW):
            in_range += 1
    candidates.append((guess, in_range, total))
print("\nLoad-base candidates (vector entries falling inside the blob):")
for (g, h, t) in candidates:
    print(f"  base=0x{g:08x}  hits={h}/{t}")
LOAD_BASE = max(candidates, key=lambda x: (x[1], -x[0]))[0]
print(f"\nUsing LOAD_BASE = 0x{LOAD_BASE:08x}")

def addr_to_off(addr: int) -> int:
    return addr - LOAD_BASE

# ── 2. Disassemble reset handler ───────────────────────────────────────────
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
md.detail = True
reset_off = addr_to_off(reset & ~1)
print(f"\nReset handler at file offset 0x{reset_off:x} (vaddr 0x{reset & ~1:08x}):")
print(f"=" * 70)
end = min(reset_off + 80, len(FW))
for ins in md.disasm(FW[reset_off:end], reset & ~1):
    print(f"  0x{ins.address:08x}:  {ins.mnemonic:8s} {ins.op_str}")

# ── 3. String dump ─────────────────────────────────────────────────────────
print(f"\nStrings (printable ASCII >= 6):")
print(f"=" * 70)
for m in re.finditer(rb'[\x20-\x7e]{6,}', FW):
    s = m.group().decode('ascii', errors='replace')
    if any(k in s.lower() for k in ['razer', 'joro', 'fw', 'firmware', 'version', 'usb', 'hid', 'razer']):
        print(f"  off=0x{m.start():06x}  vaddr=0x{LOAD_BASE+m.start():08x}  '{s}'")

# ── 4. Scan for class=0x10 / class=0x07 / cmd=0x0d immediate values ──────
# These tend to appear in the protocol dispatch as `cmp r0, #imm` or
# similar. Capstone gives us per-instruction immediate operands.
print(f"\nSearch for protocol cmd bytes as immediates (scanning first 64 KB):")
print(f"=" * 70)
imm_targets = {0x10, 0x0d, 0xa4, 0x8d, 0x29d}
hits = {k: [] for k in imm_targets}
for ins in md.disasm(FW[:0x10000], LOAD_BASE):
    if ins.mnemonic in ('cmp', 'cmn', 'movs', 'mov', 'movw'):
        for op in ins.operands:
            if op.type == 2 and op.imm in imm_targets:  # CS_OP_IMM = 2
                hits[op.imm].append((ins.address, ins.mnemonic, ins.op_str))
                break
for cmd_byte, locs in sorted(hits.items()):
    if locs:
        print(f"  cmd byte 0x{cmd_byte:04x} appears at {len(locs)} site(s):")
        for (a, m, o) in locs[:5]:
            print(f"    0x{a:08x}: {m} {o}")
