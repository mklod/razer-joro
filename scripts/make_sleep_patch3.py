"""
Phase 4 patch #3 — block BOTH idle paths at the dispatcher 0x0e6cc.
After `bl 0x12638` (idle-state decider — side-effects preserved, it
still runs), force an unconditional fall-through to the do-nothing
return 0x0e6fa (`pop {r4,pc}`), so NEITHER 0x0a7ec (lighter, state==1)
NOR 0x0a970 (deep, state==2) is entered → the shared relax cluster
never runs. Single 2-byte patch at region-03 0x0e6d6:
  01 28  (cmp r0,#1)  ->  10 e0  (b.n 0x0e6fa)
Built from CLEAN STOCK (isolates this single change). Recompute only
pkt[88]; leave args[5:9].
"""
import struct, hashlib
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

R3   = open(r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin','rb').read()
OFF  = 0x0e6d6
ORIG = bytes.fromhex('0128')   # cmp r0,#1
REPL = bytes.fromhex('10e0')   # b.n 0x0e6fa  (target = do-nothing return)

d = next(md.disasm(R3[OFF:OFF+2], OFF), None)
print(f"@0x{OFF:05x} orig {R3[OFF:OFF+2].hex()} = {d.mnemonic} {d.op_str}")
assert R3[OFF:OFF+2]==ORIG, f"orig mismatch {R3[OFF:OFF+2].hex()}!={ORIG.hex()}"
assert d.mnemonic=='cmp' and d.op_str.replace(' ','')=='r0,#1', "orig not cmp r0,#1"
r = next(md.disasm(REPL, OFF), None)   # branch is PC-rel: disasm AT 0xe6d6
print(f"replacement {REPL.hex()} @0x{OFF:05x} = {r.mnemonic} {r.op_str}")
assert r.mnemonic in ('b','b.n','b.w') and '0xe6fa' in r.op_str, \
       f"replacement not b #0xe6fa (got {r.mnemonic} {r.op_str})"
# sanity: 0xe6fa really is the do-nothing return `pop {r4,pc}`
ret = next(md.disasm(R3[0xe6fa:0xe6fe], 0xe6fa), None)
print(f"target 0x0e6fa = {ret.mnemonic} {ret.op_str} (expect pop {{r4, pc}})")
assert ret.mnemonic=='pop' and 'pc' in ret.op_str, "0xe6fa not a pop{..pc} return"

BLOB=r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
blob=bytearray(open(BLOB,'rb').read())
PKT=90
chunk_addr=OFF & ~63; in_chunk=OFF & 63
done=False
for fi in range(0,len(blob),PKT):
    fr=blob[fi:fi+PKT]
    if fr[6]!=0x10 or fr[7]!=0x02: continue
    a=fr[8:88]
    if a[2]!=0x03 or ((a[3]<<8)|a[4])!=chunk_addr: continue
    base=fi+8+9+in_chunk
    cur=bytes(blob[base:base+2])
    if cur!=R3[OFF:OFF+2]:
        raise SystemExit(f"blob/region mismatch {cur.hex()} vs {R3[OFF:OFF+2].hex()} ABORT")
    blob[base:base+2]=REPL
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
print(f"diff vs stock: {len(diff)} bytes at {[hex(x) for x in diff]} (expect <=3: 2 instr + 1 crc)")
