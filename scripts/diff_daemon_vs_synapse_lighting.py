"""
Byte-diff the DAEMON's dongle lighting writes vs SYNAPSE's. Both should
be class 0x0F SET_REPORT feature reports to the dongle. If the daemon's
frames don't actually change the keyboard but Synapse's do, the delta is
somewhere in here — wLength, the setup packet, the report-ID framing, or
the 90-byte Protocol30 payload itself.
"""
import struct
from scapy.all import rdpcap

def extract_0f(path, label):
    """Return list of (setup_bytes(8), payload_bytes, payload_len) for
    every SET_REPORT whose Protocol30 class is 0x0F."""
    out = []
    try:
        pk = rdpcap(path)
    except Exception as e:
        print(f"  ({label}: cannot read {path}: {e})")
        return out
    for p in pk:
        raw = bytes(p)
        if len(raw) < 29: continue
        hlen = struct.unpack('<H', raw[0:2])[0]
        if hlen + 8 > len(raw): continue
        # find the SETUP packet: bmReq=0x21 bReq=0x09
        so = None
        for o in range(max(0, hlen-6), min(hlen+12, len(raw)-8)):
            if raw[o] == 0x21 and raw[o+1] == 0x09:
                so = o
                break
        if so is None: continue
        setup = raw[so:so+8]
        wLen = struct.unpack('<H', setup[6:8])[0]
        # payload follows the setup
        payload = raw[so+8:so+8+wLen]
        if len(payload) < 8: continue
        # Protocol30 class is at payload offset 6 (after status,txid,rem2,proto,dsize)
        # but ONLY if payload is the 90-byte protocol frame. If a report-ID
        # byte is prepended, class is at offset 7.
        cls90 = payload[6] if len(payload) >= 7 else None
        cls91 = payload[7] if len(payload) >= 8 else None
        is_0f = (cls90 == 0x0F) or (cls91 == 0x0F and payload[0] == 0x00)
        if is_0f:
            out.append((bytes(setup), bytes(payload), wLen))
    return out

SYN = r'L:\PROJECTS\razer-joro\captures\synapse_dongle_lighting_u3.pcap'
DMN1 = r'L:\PROJECTS\razer-joro\captures\daemon_dongle_lighting_u1.pcap'
DMN3 = r'L:\PROJECTS\razer-joro\captures\daemon_dongle_lighting_u3.pcap'

syn = extract_0f(SYN, 'synapse')
dmn = extract_0f(DMN1, 'daemon-u1') + extract_0f(DMN3, 'daemon-u3')

print(f"SYNAPSE 0x0F frames: {len(syn)}")
print(f"DAEMON  0x0F frames: {len(dmn)}")

def show(tag, frames, n=6):
    print(f"\n=== {tag} — first {n} class-0x0F frames ===")
    for i, (setup, payload, wlen) in enumerate(frames[:n]):
        print(f"  [{i}] SETUP={setup.hex()}  wLen={wlen}")
        print(f"      payload({len(payload)}B)={payload.hex()}")

show("SYNAPSE", syn)
show("DAEMON", dmn)

# Direct structural comparison of the first few of each
print("\n=== STRUCTURAL COMPARISON ===")
if syn and dmn:
    s_setup, s_pl, s_wl = syn[0]
    d_setup, d_pl, d_wl = dmn[0]
    print(f"  SYNAPSE: wLength={s_wl}  payload_len={len(s_pl)}  setup={s_setup.hex()}")
    print(f"  DAEMON : wLength={d_wl}  payload_len={len(d_pl)}  setup={d_setup.hex()}")
    if s_wl != d_wl:
        print(f"  >>> wLength MISMATCH: synapse={s_wl} daemon={d_wl}")
    if s_setup != d_setup:
        # byte-by-byte
        for j in range(min(len(s_setup), len(d_setup))):
            if s_setup[j] != d_setup[j]:
                print(f"  >>> SETUP byte {j} differs: synapse=0x{s_setup[j]:02x} daemon=0x{d_setup[j]:02x}")
    # payload first 12 bytes
    print(f"  SYNAPSE payload[:12]={s_pl[:12].hex()}")
    print(f"  DAEMON  payload[:12]={d_pl[:12].hex()}")
    # is the daemon payload shifted by 1 (leading report-id byte)?
    if len(d_pl) >= 1 and d_pl[0] == 0x00 and len(d_pl) > len(s_pl):
        print(f"  >>> DAEMON payload looks 1 byte LONGER — likely a prepended report-ID 0x00")
        print(f"      daemon[1:13]={d_pl[1:13].hex()}  (compare to synapse[:12])")
