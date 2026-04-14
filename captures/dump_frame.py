#!/usr/bin/env python3
"""Dump raw bytes of specific frames from the capture for sanity-checking
our parser against the actual wire data."""
import struct
import sys
from pathlib import Path

PCAP = Path(__file__).parent / "synapse_f4_u3.pcap"
TARGETS = {15149, 15155, 15177, 51861}

data = PCAP.read_bytes()
offset = 24
n = 0
while offset + 16 <= len(data):
    ts_s, ts_us, incl_len, orig_len = struct.unpack_from("<IIII", data, offset)
    offset += 16
    pkt = data[offset:offset + incl_len]
    offset += incl_len
    n += 1
    if n in TARGETS:
        print(f"\n=== frame {n} ({incl_len} bytes) ===")
        hl = struct.unpack_from("<H", pkt, 0)[0]
        print(f"USBPcap header_len = {hl}")
        # Dump first 128 bytes in hex
        for i in range(0, min(160, len(pkt)), 16):
            row = " ".join(f"{b:02x}" for b in pkt[i:i+16])
            print(f"  {i:04x}: {row}")
        # Parse USBPcap header
        fmt = "<HQIHBHHBBI"
        if hl >= struct.calcsize(fmt):
            (_hl, _irp, _st, _fn, info, bus, dev, ep, tx, dl) = struct.unpack_from(fmt, pkt, 0)
            print(f"  info=0x{info:02x} bus={bus} dev={dev} ep=0x{ep:02x} tx=0x{tx:02x} dl={dl}")
        # Setup packet (8 bytes right after header)
        if len(pkt) >= hl + 8:
            setup = pkt[hl:hl + 8]
            bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", setup)
            print(f"  setup: bmRT=0x{bmRT:02x} bReq=0x{bReq:02x} wVal=0x{wVal:04x} wIdx=0x{wIdx:04x} wLen=0x{wLen:04x}")
        # Razer 90-byte starts at header + 8
        razer = pkt[hl + 8:hl + 8 + 90]
        if len(razer) >= 90:
            print(f"  razer[0..16] = {' '.join(f'{b:02x}' for b in razer[:16])}")
            print(f"  razer[8..24] = {' '.join(f'{b:02x}' for b in razer[8:24])}")
