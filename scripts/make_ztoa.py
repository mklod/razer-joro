"""
Phase 4 entry — produce a one-byte-modified DFU blob: remap Z -> 'A'.

1. Build expected base keymap: keymap[matrix_index] = HID usage, from
   JORO_MATRIX_TABLE (keys.rs). Z is matrix 0x48 -> usage 0x1D; A=0x04.
2. Locate the keymap in the firmware by a distinctive content signature
   (QWERTYUIOP at matrix 0x11..0x1A = HID 14 1A 08 15 17 1C 18 0C 12 13).
   Search region bins (D=9) at stride 1 and 2.
3. From the hit, derive table base+stride, compute Z's byte region-off,
   verify it == 0x1D.
4. Map region-offset -> the exact captured 10:02 chunk frame + byte in
   fwupdate_stock_replay.bin; flip 0x1D->0x04; recompute ONLY the Razer
   Protocol30 packet CRC at packet[88] (XOR of bytes [2..88)); leave the
   mystery args[5:9] EXACTLY as captured. Write fwupdate_mod_replay.bin.
"""
import struct

# matrix index -> HID usage (from keys.rs JORO_MATRIX_TABLE + HID map)
HID = {'A':0x04,'B':0x05,'C':0x06,'D':0x07,'E':0x08,'F':0x09,'G':0x0A,
 'H':0x0B,'I':0x0C,'J':0x0D,'K':0x0E,'L':0x0F,'M':0x10,'N':0x11,'O':0x12,
 'P':0x13,'Q':0x14,'R':0x15,'S':0x16,'T':0x17,'U':0x18,'V':0x19,'W':0x1A,
 'X':0x1B,'Y':0x1C,'Z':0x1D,'1':0x1E,'2':0x1F,'3':0x20,'4':0x21,'5':0x22,
 '6':0x23,'7':0x24,'8':0x25,'9':0x26,'0':0x27,'Enter':0x28,'Escape':0x29,
 'Backspace':0x2A,'Tab':0x2B,'Space':0x2C,'Minus':0x2D,'Equal':0x2E,
 'LBracket':0x2F,'RBracket':0x30,'Backslash':0x31,'Semicolon':0x33,
 'Quote':0x34,'Grave':0x35,'Comma':0x36,'Period':0x37,'Slash':0x38,
 'CapsLock':0x39}
MATRIX = {  # subset around the signature + Z neighbourhood (from keys.rs)
 0x11:'Q',0x12:'W',0x13:'E',0x14:'R',0x15:'T',0x16:'Y',0x17:'U',0x18:'I',
 0x19:'O',0x1A:'P',0x1F:'A',0x20:'S',0x21:'D',0x22:'F',0x23:'G',0x24:'H',
 0x25:'J',0x26:'K',0x27:'L',0x48:'Z',0x49:'X',0x4A:'C',0x4B:'V',0x4C:'B',
 0x4D:'N',0x4E:'M'}
SIG = bytes(HID[MATRIX[mi]] for mi in range(0x11,0x1B))   # QWERTYUIOP
HOME = bytes(HID[MATRIX[mi]] for mi in range(0x1F,0x28))   # ASDFGHJKL
print(f"QWERTYUIOP sig = {SIG.hex()}   ASDFGHJKL = {HOME.hex()}")

REGS = {'02':r'L:\PROJECTS\razer-joro\captures\joro_region_02_at_0x7000.bin',
        '03':r'L:\PROJECTS\razer-joro\captures\joro_region_03_at_0x0000.bin',
        '04':r'L:\PROJECTS\razer-joro\captures\joro_region_04_at_0x0000.bin'}
BASE = {'02':0x7000,'03':0x0000,'04':0x0000}

hit=None
for rn,p in REGS.items():
    d=open(p,'rb').read()
    for stride in (1,2):
        if stride==1:
            i=d.find(SIG)
            while i!=-1:
                # verify home row at the implied matrix offset
                home_off=i+(0x1F-0x11)*stride
                if d[home_off:home_off+len(HOME)]==HOME:
                    hit=(rn,d,stride,i); break
                i=d.find(SIG,i+1)
        else:
            # stride 2: bytes at even spacing
            sig2=b''.join(bytes([b,0]) for b in SIG)  # guess usage,then 0
            i=d.find(SIG[0:1])
            # generic stride-2 scan
            for o in range(len(d)-2*len(SIG)):
                if all(d[o+2*k]==SIG[k] for k in range(len(SIG))):
                    hit=(rn,d,2,o); break
        if hit: break
    if hit: break

if not hit:
    print("KEYMAP SIGNATURE NOT FOUND in D=9 region bins — need different "
          "search (table may be encoded/byte-pair/elsewhere). Stop.")
    raise SystemExit(1)

rn,d,stride,qpos = hit
# qpos = byte offset of matrix-0x11 (Q) entry. table base (matrix idx 0):
tbl0 = qpos - 0x11*stride
z_off = tbl0 + 0x48*stride          # Z entry, matrix 0x48
print(f"\nKEYMAP FOUND: region {rn}, stride={stride}, Q@0x{qpos:05x}, "
      f"table[0]@0x{tbl0:05x}, Z(matrix0x48)@0x{z_off:05x} = "
      f"0x{d[z_off]:02x} (expect 0x1d)")
if d[z_off]!=0x1D:
    print("!! Z byte != 0x1D — layout/stride wrong, abort"); raise SystemExit(1)

# region-offset -> chunk in fwupdate_stock_replay.bin
BLOB=r'L:\PROJECTS\razer-joro\assets\fwupdate_stock_replay.bin'
blob=bytearray(open(BLOB,'rb').read())
PKT=90
region_tag=int(rn,16)
chunk_addr=(BASE[rn]+z_off)& ~63
in_chunk=(BASE[rn]+z_off)&63
print(f"target: region 0x{region_tag:02x} addr 0x{(BASE[rn]+z_off):05x} -> "
      f"chunk@0x{chunk_addr:04x} byte {in_chunk}")

patched=0
for fi in range(0,len(blob),PKT):
    fr=blob[fi:fi+PKT]
    if fr[6]!=0x10 or fr[7]!=0x02: continue
    a=fr[8:88]
    if a[2]!=region_tag: continue
    addr=(a[3]<<8)|a[4]
    if addr!=chunk_addr: continue
    # data starts at args[9] (D=9). byte to patch:
    bpos=fi+8+9+in_chunk
    cur=blob[bpos]
    print(f"found chunk frame @blob 0x{fi:06x} (page=0x{a[3]:02x} off=0x{a[4]:02x}), "
          f"data byte = 0x{cur:02x}")
    if cur!=0x1D:
        print("!! byte at mapped position != 0x1D; D-offset or mapping wrong, abort")
        raise SystemExit(1)
    blob[bpos]=0x04                       # Z -> 'A'
    # recompute Razer Protocol30 packet CRC: XOR of bytes [2..88), at [88]
    crc=0
    for k in range(2,88):
        crc^=blob[fi+k]
    blob[fi+88]=crc
    patched+=1
    break

if patched!=1:
    print(f"PATCH FAILED (patched={patched})"); raise SystemExit(1)
OUT=r'L:\PROJECTS\razer-joro\assets\fwupdate_mod_replay.bin'
open(OUT,'wb').write(blob)
import hashlib
print(f"\nwrote {OUT} ({len(blob)} B) sha256 {hashlib.sha256(blob).hexdigest()[:16]}")
print("ONE byte changed (Z usage 0x1d->0x04) + that frame's [88] CRC; "
      "args[5:9] left as captured. Diff vs stock = exactly 2 bytes in 1 frame.")
