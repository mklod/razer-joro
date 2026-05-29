"""Disassemble x86 code at a given VA in Ry_Online_Update_Dll. Resolves
calls to imports + relative function calls. Walks function until first
ret to find function bounds."""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PE_PATH = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents\Ry_Online_Update_Dll_ouput_v1.0.1.dll'
TARGET_VA = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x10004000
LIMIT = int(sys.argv[2], 0) if len(sys.argv) > 2 else 200

pe = pefile.PE(PE_PATH)
text = pe.sections[0]
text_data = text.get_data()
image_base = pe.OPTIONAL_HEADER.ImageBase
text_va = image_base + text.VirtualAddress

# Imports lookup
imports = {}
for entry in pe.DIRECTORY_ENTRY_IMPORT:
    dll = entry.dll.decode()
    for imp in entry.imports:
        if imp.name:
            imports[imp.address] = f"{dll}!{imp.name.decode()}"

# Strings in .rdata for resolution of pushed pointers
rdata = pe.sections[1]
rdata_data = rdata.get_data()
rdata_va = image_base + rdata.VirtualAddress

def lookup_string(va):
    if not (rdata_va <= va < rdata_va + len(rdata_data)): return None
    off = va - rdata_va
    end = rdata_data.find(b'\x00', off)
    if end == -1 or end - off > 200: return None
    s = rdata_data[off:end]
    if all(0x20 <= b <= 0x7e for b in s):
        return s.decode('ascii')
    # try wide string
    end = off
    while end < len(rdata_data) - 1 and rdata_data[end] != 0 and rdata_data[end+1] == 0:
        end += 2
    if end > off:
        s = rdata_data[off:end].decode('utf-16-le', errors='replace')
        if all(0x20 <= ord(c) <= 0x7e for c in s) and len(s) >= 3:
            return f'"{s}" (wide)'
    return None

target_off = TARGET_VA - text_va
print(f"Disassembling 0x{TARGET_VA:08x} (file off 0x{target_off:x})")
print("=" * 70)

md = Cs(CS_ARCH_X86, CS_MODE_32)
md.detail = True

count = 0
for ins in md.disasm(text_data[target_off:target_off + 4000], TARGET_VA):
    annot = ''
    if ins.mnemonic == 'call':
        for op in ins.operands:
            if op.type == 3 and op.mem.disp in imports:
                annot = f"   ; -> {imports[op.mem.disp]}"
            elif op.type == 1:  # immediate
                annot = f"   ; -> sub_{op.imm:08x}"
    elif ins.mnemonic == 'push':
        for op in ins.operands:
            if op.type == 1:
                s = lookup_string(op.imm)
                if s:
                    annot = f"   ; '{s[:60]}'"
    print(f"  0x{ins.address:08x}: {ins.bytes.hex():16s} {ins.mnemonic} {ins.op_str}{annot}")
    count += 1
    if ins.mnemonic == 'ret' or count >= LIMIT:
        break
print(f"\n[stopped after {count} instructions]")
