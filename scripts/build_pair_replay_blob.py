"""
Extract c1 frames 0..70 (Synapse session-init through Joro bond write) as
a verbatim 90-byte-per-frame replay blob for joro-dongle-pair.
The pair-window minimum (3 commands) sent SET_REPORTs successfully but
the dongle didn't actually form a bond — it likely requires the full
pre-flight (handshake + slot/poll-rate config + slot queries) to put it
in "ready-to-pair" state before accepting 0b:03 discovery + 00:41 bond.

Same pattern as assets/fwupdate_stock_replay.bin (capture-once → replay-
forever).
"""
import struct
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
OUT  = r'L:\PROJECTS\razer-joro\assets\dongle_pair_replay.bin'

frames = []
for p in rdpcap(PCAP):
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen + 8 > len(raw): continue
    so = None
    for o in range(max(0, hlen-4), min(hlen+10, len(raw)-8)):
        if raw[o]==0x21 and raw[o+1]==0x09:
            if struct.unpack('<H', raw[o+6:o+8])[0] == 90:
                so = o; break
    if so is None: continue
    rz = raw[so+8:so+8+90]
    if len(rz) != 90: continue
    frames.append(rz)

# Replay frames 0..70 (Synapse session-init through the Joro bond write).
# NOTE 2026-05-22: tried the full 484-frame replay to see if Synapse's
# post-pair init fixed the dongle lighting 0x04-timeout — it did NOT, so
# we reverted to the lean 70-frame subset (pair completes in ~6s instead
# of ~12s). The dongle lighting issue is unrelated to pair completeness.
N = 70
blob = b''.join(bytes(f) for f in frames[:N])
with open(OUT, 'wb') as f:
    f.write(blob)
print(f"wrote {OUT}  ({N} frames × 90B = {len(blob)} B)")

# Dump frame summary so we know what's in there
print("\nframes:")
for i, f in enumerate(frames[:N]):
    c, d, ds = f[6], f[7], f[5]
    star = ''
    if (c, d) == (0x0b, 0x03): star = '  <-- DISCOVERY'
    elif (c, d) == (0x00, 0x41): star = '  <-- BOND WRITE'
    elif (c, d) == (0x00, 0x42): star = '  <-- UNPAIR'
    print(f"  [{i:3d}] {c:02x}:{d:02x} ds={ds:2d} args={bytes(f[8:8+max(ds,1)]).hex()}{star}")
