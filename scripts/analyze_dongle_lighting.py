"""
Find how Synapse sends Joro LIGHTING through the dongle. The daemon's
RazerDongle::set_static_color sends:
    class=0x0F cmd=0x02 dsize=9 args=[01 00 01 00 00 01 RR GG BB]
and gets status 0x04 (bridged-RF timeout). Synapse's lighting works.
Diff: what does Synapse actually send for the same operation?

We dump EVERY class 0x0F frame + look for any device-select / routing
command that precedes them (new classes, or a 0x0F subcommand we don't
send). Also dump the frames immediately around each 0x0F to catch a
wrapper / wake / handshake.
"""
import struct, collections
from scapy.all import rdpcap

PCAPS = [
    r'L:\PROJECTS\razer-joro\captures\synapse_dongle_lighting_u1.pcap',
    r'L:\PROJECTS\razer-joro\captures\synapse_dongle_lighting_u3.pcap',
]

def extract(path):
    frames = []
    try:
        pk = rdpcap(path)
    except Exception as e:
        print(f"  (could not read {path}: {e})")
        return frames
    for p in pk:
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
        # (status, txid, class, cmd, dsize, args)
        frames.append((rz[0], rz[1], rz[6], rz[7], rz[5], bytes(rz[8:88])))
    return frames

for path in PCAPS:
    frames = extract(path)
    if not frames:
        continue
    print(f"\n===== {path} — {len(frames)} Protocol30 SET_REPORT frames =====")
    hist = collections.Counter((f[2], f[3]) for f in frames)
    print(f"distinct class:cmd: " + ", ".join(f"{c:02x}:{d:02x}×{n}" for (c,d),n in sorted(hist.items())))

    # All class 0x0F frames with full args
    print("\n--- class 0x0F (lighting) frames ---")
    for i, (st, tx, c, d, ds, a) in enumerate(frames):
        if c == 0x0F:
            print(f"  [{i:4d}] 0f:{d:02x} dsize={ds:2d} args={a[:max(ds,1)].hex()}")

    # Context: 4 frames before each 0f:02 (set_static_color equivalent)
    # and 0f:04 — to catch any device-select / routing prefix.
    print("\n--- context around 0f:02 / 0f:03 / 0f:04 (±4 frames) ---")
    light_idx = [i for i,(st,tx,c,d,ds,a) in enumerate(frames) if c==0x0F and d in (0x02,0x03,0x04)]
    shown = set()
    for li in light_idx[:12]:
        lo, hi = max(0, li-4), min(len(frames), li+2)
        for i in range(lo, hi):
            if i in shown: continue
            shown.add(i)
            st, tx, c, d, ds, a = frames[i]
            mark = '  <<<' if i == li else ''
            print(f"  [{i:4d}] {c:02x}:{d:02x} ds={ds:2d} args={a[:max(ds,1)].hex()}{mark}")
        print("  ---")

    # Any NEW class:cmd not in the daemon's known set
    known = {(0x0F,0x02),(0x0F,0x03),(0x0F,0x04),(0x0F,0x80),(0x0F,0x84),(0x0F,0x90),(0x0F,0x10),
             (0x02,0x8d),(0x02,0x0d),(0x02,0xa4),(0x02,0x8c),(0x03,0x00),
             (0x01,0x02),(0x07,0x80),(0x07,0x83),(0x07,0x84),(0x07,0x88),(0x07,0x8b),(0x07,0x95),(0x07,0x08),
             (0x00,0x04),(0x00,0x81),(0x00,0x82),(0x00,0x86),(0x00,0x87),(0x00,0xbf),(0x00,0xc5),(0x00,0x84),
             (0x00,0x85),(0x00,0x93),(0x00,0xb3),
             (0x04,0x06),(0x04,0x85),(0x04,0x86),(0x05,0x80),(0x05,0x81),(0x05,0x8a),(0x06,0x80),(0x06,0x8e),
             (0x0b,0x03),(0x00,0x41),(0x00,0x42),(0x00,0x46)}
    new = sorted(set((c,d) for (st,tx,c,d,ds,a) in frames) - known)
    if new:
        print("\n--- class:cmd NOT in daemon's known set (candidate routing/wrapper cmds) ---")
        for (c,d) in new:
            for i,(st,tx,cc,dd,ds,a) in enumerate(frames):
                if (cc,dd)==(c,d):
                    print(f"  first {c:02x}:{d:02x} @ {i}  dsize={ds} args={a[:max(ds,8)].hex()}")
                    break
