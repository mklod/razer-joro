"""
Phase 4 patch #1 — extend the deep-sleep timeout constants in
region-03 func 0x0a970 so the sleep timer effectively never arms:
  @0x0a9de: mov.w r0,#0x1f4 (4f f4 fa 70)  -> movw r0,#0xffff
  @0x0aa06: mov.w r0,#0x7d0 (4f f4 fa 60)  -> movw r0,#0xffff
Both 4-byte, same size (movw T3). Verify encodings with capstone,
self-check original bytes, map to captured chunks in the stock blob,
patch + recompute only pkt[88], leave args[5:9]. -> fwupdate_mod_replay.bin
"""
import struct, hashlib
from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_LITTLE_ENDIAN
md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_LITTLE_ENDIAN)

R3 = open(r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin','rb').read()
SITES = [(0x0a9de, bytes.fromhex('4ff4fa70'), 0x1f4),
         (0x0aa06, bytes.fromhex('4ff4fa60'), 0x7d0)]
REPL = bytes.fromhex('4ff6ff70')   # candidate: movw r0,#0xffff

# verify original disasm + replacement disasm
for ins in md.disasm(REPL, 0):
    print(f"replacement {REPL.hex()} = {ins.mnemonic} {ins.op_str}")
    assert ins.mnemonic == 'movw' and '0xffff' in ins.op_str, "bad movw encoding"
for off, orig, val in SITES:
    got = R3[off:off+4]
    dis = next(md.disasm(got, off), None)
    print(f"@0x{off:05x}: {got.hex()} = {dis.mnemonic} {dis.op_str if dis else '?'} "
          f"(expect mov.w r0,#{hex(val)})")
    assert got == orig, f"orig bytes mismatch @0x{off:05x}: {got.hex()} != {orig.hex()}"

BLOB = r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
blob = bytearray(open(BLOB,'rb').read())
PKT = 90
frames = [(i, blob[i:i+PKT]) for i in range(0, len(blob), PKT)]
chunks = [(i, f[8:88]) for i, f in frames if f[6]==0x10 and f[7]==0x02]

def patch_region03(roff, newbytes):
    """patch len(newbytes) at region-03 offset roff (base 0x0000, D=9)."""
    chunk_addr = roff & ~63
    in_chunk   = roff & 63
    assert in_chunk + len(newbytes) <= 64, "instruction straddles chunk boundary"
    for fi, a in chunks:
        if a[2]!=0x03: continue
        if ((a[3]<<8)|a[4]) != chunk_addr: continue
        base = fi + 8 + 9 + in_chunk    # packet[8:]=args, data@args[9]
        cur = bytes(blob[base:base+len(newbytes)])
        if cur != R3[roff:roff+len(newbytes)]:
            raise SystemExit(f"blob/region mismatch @0x{roff:05x}: blob={cur.hex()} "
                             f"r3={R3[roff:roff+len(newbytes)].hex()} (D-map wrong, ABORT)")
        blob[base:base+len(newbytes)] = newbytes
        # recompute Razer pkt CRC = XOR bytes[2..88) -> [88]
        crc=0
        for k in range(2,88): crc ^= blob[fi+k]
        blob[fi+88]=crc
        print(f"  patched r3@0x{roff:05x} -> blob@0x{base:06x} "
              f"({cur.hex()}->{newbytes.hex()}), frame@0x{fi:06x} pkt[88]<-0x{crc:02x}")
        return
    raise SystemExit(f"no chunk for region03 addr 0x{chunk_addr:04x}")

for off, _, _ in SITES:
    patch_region03(off, REPL)

OUT = r'L:\PROJECTS\razer-joro\assets\fwupdate_mod_replay.bin'
open(OUT,'wb').write(blob)
st = open(BLOB,'rb').read()
diff = [k for k in range(len(st)) if st[k]!=blob[k]]
print(f"\nwrote {OUT} ({len(blob)} B) sha256 {hashlib.sha256(bytes(blob)).hexdigest()[:16]}")
print(f"diff vs stock: {len(diff)} bytes "
      f"(expect <=10: 2 sites x up to 4 instr bytes + up to 2 CRC bytes)")
