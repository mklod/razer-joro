"""
Deeper analysis of the dongle-setup capture around the pair window
(~frames 33-90). For each frame:
 - dump FULL args (not just 24 bytes)
 - parse BOTH directions: host->device (SET_REPORT 0x21 0x09) AND
   device->host (GET_REPORT 0xA1 0x01 / interrupt-in) — responses carry
   the pair-result data we want to understand.
 - flag the candidate pair commands and decode 04:06's table structure.
"""
import struct, collections
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
pk = rdpcap(PCAP)

# Parse USBPcap per-packet header to get direction + the Protocol30 90B
# Direction in USBPcap header: info byte at offset 16; bit 0 = 1 means
# PDO->FDO (device->host); bit 0 = 0 means host->device. Actual structure
# may vary; the most reliable: check for the SETUP 21 09 (HOST->DEV) vs
# A1 01 (DEV->HOST) signatures, OR look at the request type byte.
frames = []  # (idx, host2dev?, class, cmd, dsize, args_full)
for p in pk:
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen < 27 or hlen + 8 > len(raw): continue
    # find a setup-like 8-byte header (bmReq + bReq + wValue + wIndex + wLength)
    # bmReq 0x21 = host->device, class, interface
    # bmReq 0xA1 = device->host, class, interface
    found = None
    for o in range(hlen-2, min(hlen+8, len(raw)-8)):
        b = raw[o]
        if b in (0x21, 0xA1) and raw[o+1] == 0x09 or (b == 0xA1 and raw[o+1] == 0x01):
            wLen = struct.unpack('<H', raw[o+6:o+8])[0]
            if wLen == 90:
                found = (o, b, raw[o+1])
                break
    if found is None:
        # try the broader scan
        for o in range(20, min(40, len(raw)-8)):
            b = raw[o]
            if (b == 0x21 and raw[o+1] == 0x09) or (b == 0xA1 and raw[o+1] in (0x01, 0x09)):
                wLen = struct.unpack('<H', raw[o+6:o+8])[0]
                if wLen == 90:
                    found = (o, b, raw[o+1])
                    break
    if found is None: continue
    so, bm, br = found
    h2d = (bm == 0x21)
    rz_off = so + 8
    if rz_off + 90 > len(raw):
        # device->host data may be in a separate URB; skip if no payload here
        continue
    rz = raw[rz_off:rz_off + 90]
    frames.append((len(frames), h2d, rz[6], rz[7], rz[5], bytes(rz[8:88])))

h2d = sum(1 for f in frames if f[1])
d2h = sum(1 for f in frames if not f[1])
print(f"{len(frames)} Protocol30 frames: {h2d} host->dev (SET_REPORT), {d2h} dev->host (GET_REPORT)")

# Dump full 80-byte args for the candidate pair-window frames
CANDS = [33, 34, 35, 36, 37, 53, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 65,
         15, 18, 31, 81, 84, 85]
print("\n=== Full args (80B) at candidate pair-window frames ===")
for f in frames:
    idx, h, c, d, ds, a = f
    if idx in CANDS:
        dir_ = 'H->D' if h else 'D->H'
        nz_len = len(a.rstrip(b'\x00'))
        print(f"[{idx:4d}] {dir_} {c:02x}:{d:02x} ds={ds:2d} (nz={nz_len:2d}B) args={a.hex()}")

# Decode 04:06's table structure (38 bytes of structured (slot,code,...))
for f in frames:
    if f[2] == 0x04 and f[3] == 0x06:
        a = f[5][:f[4]]
        print(f"\n=== 04:06 table decode (dsize={f[4]}B) ===")
        # try 6-byte / 7-byte entries
        for stride in (6, 7, 8):
            print(f" stride={stride}:")
            for off in range(0, len(a), stride):
                e = a[off:off+stride]
                if len(e) < stride: break
                # interpret as (u8 slot, u16-LE code_a, u16-LE code_b, u8/u16 pad)
                if stride >= 5:
                    slot = e[0]
                    ca = struct.unpack('<H', e[1:3])[0]
                    cb = struct.unpack('<H', e[3:5])[0]
                    pad = e[5:].hex()
                    print(f"   slot={slot}: code_a=0x{ca:04x}({ca}) code_b=0x{cb:04x}({cb}) pad={pad}")
            break  # only show one stride interpretation
        break

# device-class scan: is there a recognizable "device id" byte sequence
# repeating across the pair window? The 0xda byte in 00:41 — does it
# appear in any other frame's args (e.g., as a response from the dongle
# the host then echoed)?
print("\n=== 0xda byte appearance scan (the 00:41 device-id candidate) ===")
hits = 0
for f in frames:
    idx, h, c, d, ds, a = f
    if 30 <= idx <= 90 and 0xda in a[:ds]:
        pos = list(i for i,b in enumerate(a[:ds]) if b == 0xda)
        print(f"  [{idx:4d}] {'H->D' if h else 'D->H'} {c:02x}:{d:02x} 0xda at {pos}  ds={ds}")
        hits += 1
        if hits >= 20: break

print("\n=== Direction breakdown by class (D->H responses esp. revealing) ===")
by_dir_cls = collections.Counter()
for f in frames:
    dir_ = 'D->H' if not f[1] else 'H->D'
    by_dir_cls[(dir_, f[2])] += 1
for (dir_, c), n in sorted(by_dir_cls.items()):
    print(f"  {dir_} class=0x{c:02x}: x{n}")
