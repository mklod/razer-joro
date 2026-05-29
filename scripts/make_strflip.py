"""
Build assets/fwupdate_mod_replay.bin: stock blob with ONE behavioural
byte changed — USB product string 'Razer Joro' -> 'Razer Jor0'
(last 'o' 0x6F -> '0' 0x30). Plus that frame's Razer packet CRC ([88]).
args[5:9] left EXACTLY as captured (so flash result also tells us if
that field is an enforced CRC). Self-verifies by content before patch.
"""
import struct, hashlib

BLOB=r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
OUT =r'L:\PROJECTS\razer-joro\assets\fwupdate_mod_replay.bin'
blob=bytearray(open(BLOB,'rb').read())
PKT=90
frames=[(i,blob[i:i+90]) for i in range(0,len(blob),90)]
chunks=[(i,f[8:88]) for i,f in frames if f[6]==0x10 and f[7]==0x02]

TARGET=b'Razer Joro'
# reconstruct region 0x04 at D=9, find the product string
rec=sorted([((a[3]<<8)|a[4], a[9:9+struct.unpack('<H',a[0:2])[0]])
            for fi,a in chunks if a[2]==0x04])
r04=b''.join(d for _,d in rec)
pos=r04.find(b'Razer\x00Razer Joro\x00')   # the descriptor block, unambiguous
if pos==-1:
    pos=r04.find(b'Razer Joro\x00')
    if pos==-1: raise SystemExit("product string not found in region 04 D=9")
else:
    pos=r04.find(b'Razer Joro', pos)
last_o_off = pos + len(TARGET) - 1          # region-04 offset of final 'o'
print(f"region04 D=9: 'Razer Joro' @0x{pos:05x}, "
      f"context={r04[pos-2:pos+16]!r}, last 'o' @0x{last_o_off:05x} "
      f"= 0x{r04[last_o_off]:02x} (expect 0x6f)")
assert r04[last_o_off]==0x6F, "last char not 'o' (0x6f) — layout off, abort"

# map region-04 offset -> the captured 10:02 chunk + byte (D=9: data@args[9])
chunk_addr = last_o_off & ~63
in_chunk   = last_o_off & 63
hit=None
for fi,a in chunks:
    if a[2]!=0x04: continue
    if ((a[3]<<8)|a[4])!=chunk_addr: continue
    hit=fi; break
if hit is None: raise SystemExit(f"no chunk for region04 addr 0x{chunk_addr:04x}")
bpos = hit + 8 + 9 + in_chunk               # packet[8:]=args, data@args[9]

# SELF-VERIFY by content: rebuild 'Razer Joro' straddling from this chunk
# (read 16 bytes around bpos directly from the blob)
window = bytes(blob[bpos-9:bpos+7])
print(f"chunk frame @0x{hit:06x} (pg=0x{blob[hit+8+3]:02x} off=0x{blob[hit+8+4]:02x}), "
      f"byte@0x{bpos:06x}=0x{blob[bpos]:02x}  window={window!r}")
assert blob[bpos]==0x6F, "mapped byte != 0x6f — D-map wrong, ABORT (no patch)"
assert window.endswith(b'Razer Jor'+bytes([0x6F])) or b'Razer Jor' in window, \
       "content self-check failed — ABORT"

# patch: 'o'(0x6f) -> '0'(0x30); recompute Razer packet CRC = XOR bytes[2..88)
blob[bpos]=0x30
crc=0
for k in range(2,88):
    crc ^= blob[hit+k]
old_crc=blob[hit+88]
blob[hit+88]=crc
open(OUT,'wb').write(blob)
print(f"\nPATCHED: blob[0x{bpos:06x}] 0x6f->0x30 ('Razer Joro'->'Razer Jor0'); "
      f"frame[88] CRC 0x{old_crc:02x}->0x{crc:02x}; args[5:9] UNCHANGED")
print(f"wrote {OUT} ({len(blob)} B) sha256 {hashlib.sha256(blob).hexdigest()[:16]}")
# diff vs stock = exactly 2 bytes
st=open(BLOB,'rb').read()
diff=[k for k in range(len(st)) if st[k]!=blob[k]]
print(f"diff vs stock: {len(diff)} bytes at {[hex(x) for x in diff]} "
      f"({'OK=2: char+crc' if len(diff)==2 else 'UNEXPECTED'})")
