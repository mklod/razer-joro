#!/usr/bin/env python3
"""Print ALL Razer control transfers in a frame range, no filtering."""
import struct, sys
from pathlib import Path

PCAP = Path(sys.argv[1])
START = int(sys.argv[2])
END = int(sys.argv[3])

def parse_pcap(path):
    data = path.read_bytes()
    offset = 24
    n = 0
    while offset + 16 <= len(data):
        ts_s, ts_us, incl_len, orig_len = struct.unpack_from("<IIII", data, offset)
        offset += 16
        pkt = data[offset:offset + incl_len]
        offset += incl_len
        n += 1
        yield n, pkt

def parse_usbpcap(pkt):
    if len(pkt) < 2: return None
    header_len = struct.unpack_from("<H", pkt, 0)[0]
    fmt = "<HQIHBHHBBI"
    if header_len < struct.calcsize(fmt): return None
    return struct.unpack_from(fmt, pkt, 0)[8], pkt[header_len:]

for n, pkt in parse_pcap(PCAP):
    if n < START or n > END:
        if n > END: break
        continue
    u = parse_usbpcap(pkt)
    if not u: continue
    transfer, payload = u
    if transfer != 0x02 or len(payload) < 8: continue
    bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", payload[:8])
    if bReq not in (0x09, 0x01): continue
    if wLen != 0x5a: continue
    if len(payload) < 98: continue
    data = payload[8:98]
    req = "SET" if bReq == 0x09 else "GET"
    cls = data[6]
    cmd = data[7]
    dsize = data[5]
    args = " ".join(f"{b:02x}" for b in data[8:18])
    print(f"f={n:>5} {req} cls=0x{cls:02x} cmd=0x{cmd:02x} ds={dsize:>2} args={args}")
