"""Disassemble blob at given file offset to check if real code."""
import sys, struct
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN

OFF = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0xc
N = int(sys.argv[2], 0) if len(sys.argv) > 2 else 30

FW = open(r'L:\PROJECTS\razer-joro\captures\joro_firmware.bin', 'rb').read()
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)
print(f"Disassembling offset 0x{OFF:x}, {N} instructions:")
print('=' * 60)
count = 0
for ins in md.disasm(FW[OFF:OFF+200], OFF):
    print(f"  0x{ins.address:08x}:  {ins.bytes.hex():<8} {ins.mnemonic:8s} {ins.op_str}")
    count += 1
    if count >= N:
        break
