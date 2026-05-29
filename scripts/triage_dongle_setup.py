"""
Triage dongle_setup_full_u1.pcap to find the JORO-PAIR commands. The
capture spans Synapse startup + mouse activity + the actual Joro pair +
post-pair init. We want host->dongle Protocol30 frames (90-byte HID
SET_REPORT) — especially class:cmd combos we have NOT seen before
(those are the pairing-specific commands).

Known-uninteresting class:cmd from prior captures:
  - lighting/VARSTORE: 0x0F:0x02/0x04/0x03/0x10/0x80/0x84/0x90
  - keymap: 0x02:0x8d/0x0d/0xa4, 0x03:0x00
  - mode: 0x01:0x02/0x82
  - power/idle: 0x07:0x80/0x83/0x84/0x88/0x8b/0x95/0x08
  - device info / handshake: 0x00:0x04/0x81/0x82/0x86/0x87/0xbf/0xc5
NEW class:cmd = pair-relevant candidates.
"""
import struct, collections
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
pk = rdpcap(PCAP)

# Parse USBPcap header to get device address + the Protocol30 frame
frames = []  # (idx, devaddr, wIndex, class, cmd, dsize, args[:24])
for p in pk:
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen < 27 or hlen + 8 > len(raw): continue
    dev = struct.unpack('<H', raw[19:21])[0]
    so = None
    for o in range(hlen-2, min(hlen+6, len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09: so=o; break
    if so is None:
        for o in range(20, min(40, len(raw)-8)):
            if raw[o]==0x21 and raw[o+1]==0x09: so=o; break
    if so is None: continue
    wIdx = struct.unpack('<H', raw[so+4:so+6])[0]
    wLen = struct.unpack('<H', raw[so+6:so+8])[0]
    if wLen != 90: continue
    rz = raw[so+8:so+8+90]
    frames.append((len(frames), dev, wIdx, rz[6], rz[7], rz[5], bytes(rz[8:8+24])))

print(f"{len(frames)} Protocol30 frames total")
hist = collections.Counter((f[3], f[4]) for f in frames)
print(f"\n{len(hist)} distinct class:cmd combos:")
KNOWN = {(0x0F,0x02),(0x0F,0x03),(0x0F,0x04),(0x0F,0x10),(0x0F,0x80),(0x0F,0x84),(0x0F,0x90),
        (0x02,0x8d),(0x02,0x0d),(0x02,0xa4),(0x03,0x00),
        (0x01,0x02),(0x01,0x82),
        (0x07,0x80),(0x07,0x83),(0x07,0x84),(0x07,0x88),(0x07,0x8b),(0x07,0x95),(0x07,0x08),
        (0x00,0x04),(0x00,0x81),(0x00,0x82),(0x00,0x86),(0x00,0x87),(0x00,0xbf),(0x00,0xc5),
        (0x00,0x84)}
for (c,d),n in sorted(hist.items()):
    tag = '' if (c,d) in KNOWN else '   <<< NEW (pair-relevant?)'
    print(f"  {c:02x}:{d:02x}  x{n}{tag}")

# device-address grouping — the dongle is one devaddr; the mouse may
# share or be different. Show distinct devaddrs and their frame counts
da = collections.Counter(f[1] for f in frames)
print(f"\ndev addrs seen: " + ", ".join(f"dev{a}×{n}" for a,n in da.most_common(5)))

# Dump first occurrence of each NEW class:cmd with args
print("\n=== First occurrence of each NEW class:cmd (full 24-byte args) ===")
seen = set()
for idx, dev, wi, c, d, ds, a in frames:
    if (c,d) in KNOWN or (c,d) in seen: continue
    seen.add((c,d))
    print(f"  [{idx:5d}] dev={dev:3d} wIdx=0x{wi:04x}  {c:02x}:{d:02x} dsize={ds:2d} args={a.hex()}")

# show the ordered (run-length collapsed) sequence around any NEW
new_classes = {(c,d) for c,d in hist if (c,d) not in KNOWN}
if new_classes:
    print(f"\n=== Context around first NEW class:cmd (±10 frames) ===")
    first_new_idx = min(idx for idx,_,_,c,d,_,_ in frames if (c,d) in new_classes)
    lo, hi = max(0, first_new_idx-10), min(len(frames), first_new_idx+25)
    for idx, dev, wi, c, d, ds, a in frames[lo:hi]:
        flag = '  *NEW' if (c,d) in new_classes else ''
        print(f"  [{idx:5d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,8)].hex()}{flag}")
