"""
PHASE 0 — settle signature-vs-CRC (gates the whole custom-FW track).

(A) Enumerate EVERY non-chunk DFU control command Razer's updater sent
    in fw_update_u1.pcap, with full 80-byte args. If a cryptographic
    signature/hash were required, the updater would have to transmit it
    (P-256 sig = 64 B, SHA-256 = 32 B). If the only non-chunk traffic is
    short addr/size/CRC + status polls, the image is accepted on
    integrity (CRC) alone -> modified firmware is flashable.
(B) Scan all 3 plaintext regions for crypto primitives that a signature
    verifier needs: SHA-256 IV + round constants, AES S-box, P-256/
    secp256r1 constants. Presence in the app != proof (bootloader is
    separate & uncaptured), but ABSENCE corroborates (A).
"""
import struct, collections
from scapy.all import rdpcap

PCAP=r'L:\PROJECTS\razer-joro\captures\fw_update_u1.pcap'
pkts=rdpcap(PCAP)
cmds=[]
for p in pkts:
    raw=bytes(p)
    if len(raw)<27+8+90: continue
    so=None
    for off in range(20,min(40,len(raw)-8)):
        if raw[off]==0x21 and raw[off+1]==0x09: so=off;break
    if so is None: continue
    if struct.unpack('<H',raw[so+6:so+8])[0]!=90: continue
    rz=raw[so+8:so+8+90]
    cls,cmd=rz[6],rz[7]
    cmds.append((cls,cmd,rz[5],rz[8:88]))

hist=collections.Counter((c,d) for c,d,_,_ in cmds)
print(f"fw_update_u1.pcap: {len(cmds)} Protocol30 frames")
print("class:cmd histogram:")
for (c,d),n in sorted(hist.items()):
    print(f"  {c:02x}:{d:02x}  ×{n}")

print("\n=== ALL non-chunk DFU commands (class!=0x10/cmd!=0x02), full args ===")
shown=collections.Counter()
for c,d,ds,a in cmds:
    if c==0x10 and d==0x02:  # firmware chunk, skip
        continue
    k=(c,d,ds,a.hex())
    shown[k]+=1
for (c,d,ds,ah),n in sorted(shown.items()):
    nz=bytes.fromhex(ah).rstrip(b'\x00')
    print(f"  class=0x{c:02x} cmd=0x{d:02x} dsize={ds:2d} ×{n}  "
          f"args(nonzero {len(nz)}B)={nz.hex() or '(all zero)'}")

# longest single arg payload seen anywhere (a signature would be >=32B)
maxlen=0; maxinfo=None
for c,d,ds,a in cmds:
    if c==0x10 and d==0x02: continue
    nz=len(a.rstrip(b'\x00'))
    if nz>maxlen: maxlen=nz; maxinfo=(c,d,ds)
print(f"\nLongest non-chunk payload: {maxlen} nonzero bytes "
      f"{maxinfo}  (P-256 sig=64B, SHA-256=32B — anything <16B = CRC/addr only)")

# (B) crypto-primitive scan
import binascii
SHA256_IV=[0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
           0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19]
SHA256_K0=[0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5]
AES_SBOX=bytes([0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b])
P256_PRIME_TAIL=b'\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff\xff'  # weak; use known b
P256_B=binascii.unhexlify('5ac635d8aa3a93e7b3ebbd55769886bc651d06b0cc53b0f63bce3c3e27d2604b')

for tag in ('02_at_0x7000','03_at_0x0000','04_at_0x0000'):
    d=open(rf'L:\PROJECTS\razer-joro\captures\joro_region_{tag}.bin','rb').read()
    le=[struct.unpack('<I',d[i:i+4])[0] for i in range(0,len(d)-3,4)]
    has_iv=all(v in le for v in SHA256_IV)
    has_k =all(v in le for v in SHA256_K0)
    has_sbox = AES_SBOX in d
    has_p256 = P256_B in d
    crc32=0xedb88320 in le or 0x04c11db7 in le or \
           any(0x04c11db7==struct.unpack('>I',d[i:i+4])[0] for i in range(0,min(len(d)-3,4096),4))
    print(f"\nregion {tag}: SHA256-IV={has_iv} SHA256-K={has_k} "
          f"AES-Sbox={has_sbox} P256-b={has_p256}  (CRC32-poly seen={crc32})")
