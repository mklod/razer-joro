"""
Phase 4 patch #2 — neutralize the deep power-save entry.
0x0e6cc state-dispatch: state==2 -> (bl 0xa8ec) -> b.w 0x0a970 (deep
relax/power-save, residual-lag cause). Replace the tail-call at
region-03 0x0e6ea:
  fc f7 41 b9  (b.w #0xa970)  ->  70 47 00 bf  (bx lr ; nop)
lr was restored by the preceding pop.w {r4,lr} @0x0e6e6, so bx lr is a
clean function return == the state!=1,2 path. Keyboard never enters
deep power-save; lighter idle path (state==1 -> 0xa7ec) untouched.
Built from CLEAN STOCK (isolates this single change). Recompute only
pkt[88]; leave args[5:9].
"""
import struct, hashlib
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

R3   = open(r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin','rb').read()
OFF  = 0x0e6ea
ORIG = bytes.fromhex('fcf741b9')   # b.w #0xa970
REPL = bytes.fromhex('704700bf')   # bx lr ; nop

# verify orig + replacement via capstone
d = next(md.disasm(R3[OFF:OFF+4], OFF), None)
print(f"@0x{OFF:05x} orig {R3[OFF:OFF+4].hex()} = {d.mnemonic} {d.op_str}")
assert R3[OFF:OFF+4]==ORIG, f"orig mismatch: {R3[OFF:OFF+4].hex()} != {ORIG.hex()}"
assert d.mnemonic in ('b','b.w') and '0xa970' in d.op_str, "orig not b.w #0xa970"
parts=list(md.disasm(REPL,0))
print("replacement decodes:", ", ".join(f"{x.mnemonic} {x.op_str}".strip() for x in parts))
assert parts[0].mnemonic=='bx' and 'lr' in parts[0].op_str, "repl[0] not bx lr"

# patch from CLEAN STOCK blob
BLOB=r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
blob=bytearray(open(BLOB,'rb').read())
PKT=90
chunks=[(i,blob[i:i+PKT]) for i in range(0,len(blob),PKT)]
chunk_addr=OFF & ~63; in_chunk=OFF & 63
assert in_chunk+4<=64,"straddles chunk"
done=False
for fi,fr in chunks:
    if fr[6]!=0x10 or fr[7]!=0x02: continue
    a=fr[8:88]
    if a[2]!=0x03 or ((a[3]<<8)|a[4])!=chunk_addr: continue
    base=fi+8+9+in_chunk            # packet[8:]=args, data@args[9] (D=9)
    cur=bytes(blob[base:base+4])
    if cur!=R3[OFF:OFF+4]:
        raise SystemExit(f"blob/region mismatch @0x{OFF:05x}: {cur.hex()} vs {R3[OFF:OFF+4].hex()} ABORT")
    blob[base:base+4]=REPL
    crc=0
    for k in range(2,88): crc^=blob[fi+k]
    blob[fi+88]=crc
    print(f"patched r3@0x{OFF:05x} -> blob@0x{base:06x} ({cur.hex()}->{REPL.hex()}), "
          f"frame@0x{fi:06x} pkt[88]<-0x{crc:02x}")
    done=True; break
if not done: raise SystemExit("target chunk not found")

OUT=r'L:\PROJECTS\razer-joro\assets\fwupdate_mod_replay.bin'
open(OUT,'wb').write(blob)
st=open(BLOB,'rb').read()
diff=[k for k in range(len(st)) if st[k]!=blob[k]]
print(f"wrote {OUT} ({len(blob)} B) sha256 {hashlib.sha256(bytes(blob)).hexdigest()[:16]}")
print(f"diff vs stock: {len(diff)} bytes at {[hex(x) for x in diff]} (expect <=5: 4 instr + 1 crc)")
