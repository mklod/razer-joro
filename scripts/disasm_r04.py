"""
Disassemble region-04 functions (the shared relax-action externs called
by both idle paths). Unified map: 02|03|04 contiguous, region03 base
0x9000 → region04 base = 0x9000 + len(R03). A region-03 `bl 0x12d40`
targets combined 0x9000+0x12d40; minus region04 base = region04 off
0x2d40. Here we disassemble region04 at the requested offsets and
annotate bl (resolved into the combined map) + svc (SoftDevice — BLE
conn-param relax is the suspect).
"""
import sys, struct, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC

R02=open(r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin','rb').read()
R03=open(r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin','rb').read()
R04=open(r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin','rb').read()
# combined 02|03|04
SP02,SP03,SP04 = (0,len(R02)),(len(R02),len(R02)+len(R03)),(len(R02)+len(R03),len(R02)+len(R03)+len(R04))
COMB = R02+R03+R04
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.detail=True
PRO16={0x80,0x90,0xb0,0x10,0x70,0xf0,0xf7,0xf8,0x30,0x00,0x08,0x38}
def isp(b,o): return o+1<len(b) and ((b[o+1]==0xb5 and b[o] in PRO16) or (b[o]==0x2d and b[o+1]==0xe9))
def cloc(a):
    if SP02[0]<=a<SP02[1]: return f"r02+0x{a-SP02[0]:05x}"
    if SP03[0]<=a<SP03[1]: return f"r03+0x{a-SP03[0]:05x}"
    if SP04[0]<=a<SP04[1]: return f"r04+0x{a-SP04[0]:05x}"
    return f"OOB0x{a:08x}"

def dis(r04off, n=48):
    print(f"\n=== region04 @0x{r04off:05x} (combined 0x{SP04[0]+r04off:06x}) ===")
    addr=r04off
    cnt=0
    while cnt<n and addr<len(R04):
        g=list(md.disasm(R04[addr:addr+4],addr))
        if not g:
            print(f"  0x{addr:05x}: {R04[addr:addr+2].hex()}  .short"); addr+=2; cnt+=1; continue
        i=g[0]; note=''
        if i.mnemonic=='svc':
            note=f"   <<< SVC {i.op_str} (SoftDevice — BLE/radio if >=0x60)"
        elif i.mnemonic in('bl','blx','b','b.w'):
            for op in i.operands:
                if op.type==ARM_OP_IMM:
                    # region04 instr at combined SP04[0]+addr; bl offset
                    # position-independent → combined target:
                    ct=(SP04[0]+addr + (op.imm-addr)) & 0xffffffff
                    note=f"   ; -> {cloc(ct)}"
        elif i.mnemonic in('ldr','ldr.w'):
            for op in i.operands:
                if op.type==ARM_OP_MEM and op.mem.base==ARM_REG_PC:
                    la=((i.address+4)&~3)+op.mem.disp
                    if 0<=la+4<=len(R04):
                        w=struct.unpack('<I',R04[la:la+4])[0]
                        note=f"   ; lit=0x{w:08x}"
                        if 0x40000000<=w<0x40020000 or 0x50000000<=w<0x50010000:
                            note+=" <PERIPH>"
        print(f"  0x{addr:05x}: {i.bytes.hex():<10s} {i.mnemonic} {i.op_str}{note}")
        cnt+=1
        if (i.mnemonic=='pop' and 'pc' in i.op_str) or (i.mnemonic=='bx' and 'lr' in i.op_str):
            print("  [epilogue]"); break
        addr+=i.size

if len(sys.argv) > 1:
    for a in sys.argv[1:]:
        dis(int(a, 16), 60)
else:
    for off in (0x2d40, 0x2e34, 0x3a44):
        dis(off, 44)
