"""
Which USB interface (wIndex) does Synapse send the dongle lighting
frames (class 0x0F) to? The daemon's RazerDongle opens MI_00 (iface 0).
If Synapse targets a different interface, the daemon is writing to the
wrong endpoint and the keyboard never gets the lighting command.

Also dump device address so we confirm it's the dongle (PID 0x009C).
"""
import struct, collections
from scapy.all import rdpcap

PCAPS = [
    r'L:\PROJECTS\razer-joro\captures\synapse_dongle_lighting_u3.pcap',
    r'L:\PROJECTS\razer-joro\captures\synapse_dongle_lighting_u1.pcap',
]

for path in PCAPS:
    try:
        pk = rdpcap(path)
    except Exception as e:
        print(f"skip {path}: {e}")
        continue
    print(f"\n===== {path} =====")
    # (class,cmd) -> Counter of wIndex
    iface_by_cmd = collections.defaultdict(collections.Counter)
    dev_by_cmd = collections.defaultdict(collections.Counter)
    n = 0
    for p in pk:
        raw = bytes(p)
        if len(raw) < 29: continue
        hlen = struct.unpack('<H', raw[0:2])[0]
        if hlen + 8 > len(raw): continue
        # USBPcap header: device address at offset 19-20 (u16-LE)
        dev = struct.unpack('<H', raw[19:21])[0] if len(raw) >= 21 else 0
        so = None
        for o in range(max(0, hlen-4), min(hlen+10, len(raw)-8)):
            if raw[o]==0x21 and raw[o+1]==0x09:
                if struct.unpack('<H', raw[o+6:o+8])[0] == 90:
                    so = o; break
        if so is None: continue
        # SETUP packet: bmRequestType(1) bRequest(1) wValue(2) wIndex(2) wLength(2)
        wValue = struct.unpack('<H', raw[so+2:so+4])[0]
        wIndex = struct.unpack('<H', raw[so+4:so+6])[0]
        rz = raw[so+8:so+8+90]
        if len(rz) != 90: continue
        c, d = rz[6], rz[7]
        iface_by_cmd[(c,d)][(wValue, wIndex)] += 1
        dev_by_cmd[(c,d)][dev] += 1
        n += 1
    print(f"{n} Protocol30 SET_REPORT frames")
    print("\nclass:cmd -> (wValue, wIndex) distribution:")
    for (c,d) in sorted(iface_by_cmd):
        wi = iface_by_cmd[(c,d)]
        dv = dev_by_cmd[(c,d)]
        wi_str = ", ".join(f"wVal=0x{v:04x}/wIdx=0x{i:04x}×{n}" for (v,i),n in wi.most_common())
        dv_str = ", ".join(f"dev{a}×{n}" for a,n in dv.most_common(3))
        tag = '  <<< LIGHTING' if c == 0x0F else ''
        print(f"  {c:02x}:{d:02x}  {wi_str}  [{dv_str}]{tag}")
