"""
Synapse cold-launch → connect → dongle lighting capture. Find what
Synapse does at CONNECT time that enables dongle lighting control —
the prerequisite the daemon is missing. The daemon's 0f:* frames are
byte-identical to Synapse's, so the enabler is a non-0f command (or a
specific 0f subcommand) in the connect handshake.
"""
import struct, collections
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\synapse_cold_lighting_u3.pcap'
pk = rdpcap(PCAP)

frames = []  # (class, cmd, dsize, args)
for p in pk:
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen + 8 > len(raw): continue
    so = None
    for o in range(max(0, hlen-6), min(hlen+12, len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09:
            if struct.unpack('<H', raw[o+6:o+8])[0] == 90:
                so = o; break
    if so is None: continue
    rz = raw[so+8:so+8+90]
    if len(rz) != 90: continue
    frames.append((rz[6], rz[7], rz[5], bytes(rz[8:88])))

print(f"{len(frames)} Protocol30 frames")

# Find first 0f:03 (the first actual color write)
first_color = next((i for i,(c,d,ds,a) in enumerate(frames) if c==0x0F and d==0x03), None)
print(f"first 0f:03 (color) at idx {first_color}")

# class:cmd histogram for the whole capture
hist = collections.Counter((c,d) for c,d,ds,a in frames)
print(f"\n{len(hist)} distinct class:cmd:")
for (c,d),n in sorted(hist.items()):
    print(f"  {c:02x}:{d:02x} x{n}")

# What the DAEMON sends (its known command set on the dongle path):
#   0f:80/84/90/10/02/03/04 (lighting), 02:a4/8d/0d (keymap), 03:00,
#   01:02 (mode), 07:03 (idle), 00:81 (fw). Anything ELSE Synapse sends
#   is a candidate prerequisite.
daemon_sends = {(0x0F,0x80),(0x0F,0x84),(0x0F,0x90),(0x0F,0x10),(0x0F,0x02),
                (0x0F,0x03),(0x0F,0x04),
                (0x02,0xa4),(0x02,0x8d),(0x02,0x0d),(0x03,0x00),
                (0x01,0x02),(0x07,0x03),(0x00,0x81)}
print("\n=== class:cmd Synapse sends that the daemon does NOT ===")
for (c,d),n in sorted(hist.items()):
    if (c,d) not in daemon_sends:
        # first occurrence args
        for i,(cc,dd,ds,a) in enumerate(frames):
            if (cc,dd)==(c,d):
                print(f"  {c:02x}:{d:02x} x{n}  first@{i} dsize={ds} args={a[:max(ds,8)].hex()}")
                break

# Run-length-collapsed sequence from start through the first color +5
end = (first_color + 6) if first_color is not None else min(len(frames), 120)
print(f"\n=== ordered sequence (run-length collapsed) frames 0..{end} ===")
prev = None; cnt = 0; start_i = 0
def flush(pi, key, cnt, start_i):
    c,d,ds,a = key
    print(f"  [{start_i:4d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,1)].hex():<24s} x{cnt}")
for i,(c,d,ds,a) in enumerate(frames[:end]):
    key = (c,d,ds,a[:ds])
    if key == prev:
        cnt += 1; continue
    if prev is not None:
        flush(i, prev_full, cnt, start_i)
    prev = key; prev_full = (c,d,ds,a); cnt = 1; start_i = i
if prev is not None:
    flush(end, prev_full, cnt, start_i)
