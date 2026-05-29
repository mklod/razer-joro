"""
Disassemble FWUpdaterDLL.dll's DFU functions to determine:
  - Does DFUProgram transform/encrypt the buffer before sending? (we believe
    NO — host sends .enc verbatim — verify by reading the code)
  - What does EnterDeviceMode / cmd=0x04 send? (key-exchange / handshake?)
  - What does DFUErase / cmd=0x01 init carry? (serial-derived key material?)
  - Any reference to the device serial (SN=PM2327F97100011) in key setup?

x86 (Machine 0x14c). Image base 0x10000000 (typical; confirm from PE).
Exports of interest:
  DFUProgram 0x3ff0  EnterDeviceMode 0x2c90  DFUErase 0x3e90
  DFUVerify 0x4100   SendCmd 0x43b0          GetSN 0x2e90
"""
import sys, pefile
from capstone import Cs, CS_ARCH_X86, CS_MODE_32

PE_PATH = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents\FWUpdaterDLL.dll'
pe = pefile.PE(PE_PATH)
ib = pe.OPTIONAL_HEADER.ImageBase
text = pe.sections[0]
tdata = text.get_data()
tva = ib + text.VirtualAddress

# rdata for string resolution
rd = pe.sections[1]; rdv = ib + rd.VirtualAddress; rdb = rd.get_data()

imports = {}
for e in pe.DIRECTORY_ENTRY_IMPORT:
    dll = e.dll.decode()
    for imp in e.imports:
        if imp.name:
            imports[imp.address] = f"{dll}!{imp.name.decode()}"

def s_at(va):
    if rdv <= va < rdv + len(rdb):
        o = va - rdv
        end = rdb.find(b'\x00', o)
        if 0 <= end - o < 120:
            s = rdb[o:end]
            if all(0x20 <= b <= 0x7e for b in s) and len(s) >= 3:
                return s.decode('ascii')
    return None

md = Cs(CS_ARCH_X86, CS_MODE_32); md.detail = True

EXPORTS = {
    'DFUProgram': 0x3ff0, 'EnterDeviceMode': 0x2c90, 'DFUErase': 0x3e90,
    'DFUVerify': 0x4100, 'SendCmd': 0x43b0, 'GetSN': 0x2e90,
    'GetBootloaderHandle': 0x2f80, 'OpenDevice': 0x32c0,
}
WHICH = sys.argv[1] if len(sys.argv) > 1 else 'DFUProgram'
LIMIT = int(sys.argv[2]) if len(sys.argv) > 2 else 120
rva = EXPORTS[WHICH]
off = rva - text.VirtualAddress
va  = ib + rva
print(f"=== {WHICH} @ VA 0x{va:08x} (rva 0x{rva:x}) ===")
crypto_hint = ('xor','rol','ror','aes','imul','mul ',' shl',' shr','pxor','aesenc','aesdec')
n = 0
for ins in md.disasm(tdata[off:off+6000], va):
    a = ''
    if ins.mnemonic == 'call':
        for op in ins.operands:
            if op.type == 3 and op.mem.disp in imports:
                a = f"   ; -> {imports[op.mem.disp]}"
            elif op.type == 1:
                a = f"   ; -> sub_{op.imm:08x}"
    elif ins.mnemonic == 'push' and ins.operands and ins.operands[0].type == 1:
        st = s_at(ins.operands[0].imm)
        if st: a = f"   ; '{st}'"
    flag = '  <<<' if any(h in (ins.mnemonic+' '+ins.op_str) for h in crypto_hint) else ''
    print(f"  0x{ins.address:08x}: {ins.bytes.hex():14s} {ins.mnemonic} {ins.op_str}{a}{flag}")
    n += 1
    if ins.mnemonic == 'ret' or n >= LIMIT:
        break
print(f"[{n} ins]")
