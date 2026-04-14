#!/usr/bin/env python3
"""Dump the full 90-byte Razer packet for every class=0x02 cmd=0x0d write
in a capture. Shows header + args, so we can diff base-layer vs Hypershift."""
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
    return header[8], pkt[header_len:]  # transfer, payload

for n, pkt in parse_pcap(PCAP):
    u = parse_usbpcap(pkt)
    if not u: continue
    transfer, payload = u
    if transfer != 0x02 or len(payload) < 98: continue
    setup = payload[:8]
    bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", setup)
    if bReq != 0x09 or wLen != 0x5a: continue
    data = payload[8:8 + 90]
    if len(data) < 90: continue
    # class at byte 6, cmd at byte 7
    if data[6] != 0x02 or data[7] != 0x0D: continue
    hdr = " ".join(f"{b:02x}" for b in data[:8])
    args = " ".join(f"{b:02x}" for b in data[8:18])
    print(f"f={n:>6} bmRT=0x{bmRT:02x} wVal=0x{wVal:04x} wIdx=0x{wIdx:04x}")
    print(f"        header[0..8] = {hdr}")
    print(f"        args[0..10]  = {args}")
