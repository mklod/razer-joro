"""
Locate the Lock-key Win+L emission in firmware. The keyboard sends a
HID kbd report with modifier 0x08 (Left-GUI) + usage 0x0F ('l').
Search the unified image (r02|r03|r04, r03@0x9000) for:
  A. byte pairs `08 0f` / `0f 08` (a (mod,usage) or keymap-entry pair)
  B. code loading imm 0x0F with a nearby imm 0x08 (report build)
  C. a small special-key/system-action table containing 080f
Report region+offset + disasm context so we can pin the patch site.
"""
import struct, bisect
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
from capstone.arm import ARM_OP_IMM

R02=open(r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin','rb').read()
R03=open(r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin','rb').read()
R04=open(r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin','rb').read()
SP={'r02':(0,len(R02)),'r03':(len(R02),len(R02)+len(R03)),
    'r04':(len(R02)+len(R03),len(R02)+len(R03)+len(R04))}
COMB=R02+R03+R04
md=Cs(CS_ARCH_ARM,CS_MODE_THUMB|CS_MODE_LITTLE_ENDIAN); md.detail=True
def loc(a):
    for t,(s,e) in SP.items():
        if s<=a<e: return f"{t}+0x{a-s:05x}"
    return f"?0x{a:x}"

# A: byte pairs 08 0f / 0f 08 in each region
print("=== A. byte pairs 08 0f / 0f 08 ===")
for t,buf in (('r02',R02),('r03',R03),('r04',R04)):
    for pat in (b'\x08\x0f', b'\x0f\x08'):
        i=buf.find(pat)
        cnt=0
        while i!=-1 and cnt<8:
            ctx=buf[max(0,i-6):i+8].hex()
            print(f"  {t}+0x{i:05x} [{pat.hex()}] ctx={ctx}")
            i=buf.find(pat,i+1); cnt+=1

# B: in code, `movs/mov rX,#0x0f` with a `#0x08` within +-10 instrs
print("\n=== B. imm 0x0f with nearby imm 0x08 (HID report build) ===")
for t,buf in (('r02',R02),('r03',R03),('r04',R04)):
    ins=[]
    o=0
    while o<len(buf)-1:
        g=list(md.disasm(buf[o:o+4],o))
        if g: ins.append(g[0]); o+=g[0].size
        else: o+=2
    for k,i in enumerate(ins):
        if i.mnemonic in('movs','mov','mov.w','movw','cmp','cmp.w') and i.operands:
            ims=[op.imm for op in i.operands if op.type==ARM_OP_IMM]
            if 0x0f in ims:
                # window for a 0x08 imm
                win=ins[max(0,k-8):k+9]
                if any(op.type==ARM_OP_IMM and op.imm==0x08
                       for w in win for op in w.operands):
                    seg=' | '.join(f"{w.mnemonic} {w.op_str}" for w in ins[max(0,k-3):k+4])
                    print(f"  {t}+0x{i.address:05x}: {seg}")

print("\n(>>> inspect the strongest hit; the Lock handler builds a kbd "
      "report mod=0x08 usage=0x0f — patch usage->0x4c (Delete), mod->0x00)")
