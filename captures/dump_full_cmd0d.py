#!/usr/bin/env python3
"""Dump full 80-byte args for every class=0x02 cmd=0x0d SET write."""
import struct, sys
from pathlib import Path
PCAP = Path(sys.argv[1])

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
    header = struct.unpack_from(fmt, pkt, 0)
    return header[8], pkt[header_len:]

seen = set()
for n, pkt in parse_pcap(PCAP):
    u = parse_usbpcap(pkt)
    if not u: continue
    transfer, payload = u
    if transfer != 0x02 or len(payload) < 98: continue
    bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", payload[:8])
    if bReq != 0x09 or wLen != 0x5a: continue
    data = payload[8:98]
    if data[6] != 0x02 or data[7] != 0x0D: continue
    key = bytes(data[8:18])  # first 10 args bytes
    if key in seen: continue
    seen.add(key)
    print(f"f={n:>6} trans_id=0x{data[1]:02x}")
    for i in range(0, 80, 16):
        row = " ".join(f"{b:02x}" for b in data[8+i:8+i+16])
        print(f"  args[{i:02d}..{i+15:02d}] = {row}")
    print()
