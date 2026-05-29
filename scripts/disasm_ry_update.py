"""
Disassemble Ry_Online_Update_Dll_ouput_v1.0.1.dll's appUpdateFirmware
function and look for:
  - File open / read of *.enc
  - Calls to Windows CryptoAPI / BCrypt / CNG
  - Imported AES / encryption library functions
  - XOR loops with constant keys
"""
import struct
import pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PE_PATH = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents\Ry_Online_Update_Dll_ouput_v1.0.1.dll'

pe = pefile.PE(PE_PATH)
text = pe.sections[0]
text_data = text.get_data()
image_base = pe.OPTIONAL_HEADER.ImageBase
text_va = image_base + text.VirtualAddress
print(f"Image base: 0x{image_base:08x}")
print(f".text VA=0x{text_va:08x} size=0x{len(text_data):x}")

# Build import-table lookup so we can resolve `call [import]` targets to API names
imports = {}
for entry in pe.DIRECTORY_ENTRY_IMPORT:
    dll = entry.dll.decode()
    for imp in entry.imports:
        if imp.name:
            imports[imp.address] = f"{dll}!{imp.name.decode()}"
        elif imp.ordinal is not None:
            imports[imp.address] = f"{dll}!#{imp.ordinal}"
print(f"\nImports of crypto interest (BCrypt/CryptoAPI):")
for addr, name in sorted(imports.items()):
    if any(k in name.lower() for k in ['crypt', 'aes', 'rsa', 'cng', 'bcrypt', 'wincrypt', 'rijndael']):
        print(f"  0x{addr:08x} {name}")
print()

# Find appUpdateFirmware at RVA 0x7a20
target_rva = 0x7a20
text_off = target_rva - text.VirtualAddress
target_va = image_base + target_rva
print(f"appUpdateFirmware at RVA 0x{target_rva:x}, file off 0x{text_off:x}, VA 0x{target_va:x}")
print()

md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True

# Disassemble first 100 instructions of appUpdateFirmware to see prologue + initial calls
print("=== appUpdateFirmware disassembly (first 100 ins) ===")
for i, ins in enumerate(md.disasm(text_data[text_off:text_off+800], target_va)):
    if i >= 100: break
    annot = ''
    if ins.mnemonic == 'call':
        for op in ins.operands:
            if op.type == 3:  # MEM
                addr = op.mem.disp
                if addr in imports:
                    annot = f"  ; -> {imports[addr]}"
    print(f"  0x{ins.address:08x}: {ins.bytes.hex():16s} {ins.mnemonic} {ins.op_str}{annot}")

# Also list ALL imports referenced from this function range
print()
print("=== All called imports anywhere in appUpdateFirmware (first 4KB after entry) ===")
seen_imports = []
for ins in md.disasm(text_data[text_off:text_off+0x1000], target_va):
    if ins.mnemonic == 'call':
        for op in ins.operands:
            if op.type == 3:
                addr = op.mem.disp
                if addr in imports and imports[addr] not in seen_imports:
                    seen_imports.append(imports[addr])
                    print(f"  0x{ins.address:08x}: call -> {imports[addr]}")
