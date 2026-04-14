#!/usr/bin/env python3
"""Find class=0x02 cmd=0x0d SET packets matching given matrix index,
print full 90 raw bytes."""
import struct, sys
from pathlib import Path

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

def find_0d_writes(pcap_path, matrix_idx):
    results = []
    for n, pkt in parse_pcap(Path(pcap_path)):
        u = parse_usbpcap(pkt)
        if not u: continue
        transfer, payload = u
        if transfer != 0x02 or len(payload) < 98: continue
        bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", payload[:8])
        if bReq != 0x09 or wLen != 0x5a: continue
        data = payload[8:98]
        if data[6] != 0x02 or data[7] != 0x0D: continue
        if data[9] != matrix_idx: continue
        results.append((n, bmRT, wVal, wIdx, data))
    return results

def dump_hex(label, data):
    print(f"=== {label} ===")
    for i in range(0, 90, 16):
        hexstr = " ".join(f"{b:02x}" for b in data[i:i+16])
        print(f"  [{i:02x}] {hexstr}")

# Synapse Left→Home (matrix 0x4f)
syn = find_0d_writes("captures/synapse_hypershift_u3.pcap", 0x4f)
# Daemon Left→Home (matrix 0x4f)
dmn = find_0d_writes("captures/daemon_write_u3.pcap", 0x4f)

print(f"Synapse Left→Home packets: {len(syn)}")
print(f"Daemon Left→Home packets: {len(dmn)}")
print()

if syn:
    n, bmRT, wVal, wIdx, data = syn[0]
    print(f"Synapse: frame={n} bmRT=0x{bmRT:02x} wVal=0x{wVal:04x} wIdx=0x{wIdx:04x}")
    dump_hex("Synapse Left→Home raw", data)
print()
if dmn:
    n, bmRT, wVal, wIdx, data = dmn[0]
    print(f"Daemon: frame={n} bmRT=0x{bmRT:02x} wVal=0x{wVal:04x} wIdx=0x{wIdx:04x}")
    dump_hex("Daemon Left→Home raw", data)

if syn and dmn:
    print("\n=== BYTE-BY-BYTE DIFF ===")
    for i in range(90):
        if syn[0][4][i] != dmn[0][4][i]:
            print(f"  [{i:02x}] synapse=0x{syn[0][4][i]:02x}  daemon=0x{dmn[0][4][i]:02x}")
