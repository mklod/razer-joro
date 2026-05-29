"""Dump first N interrupt-IN reports per (bus, device) so we can identify by report shape."""
import struct, sys
from collections import defaultdict


def parse_pcap(path):
    with open(path, "rb") as f:
        f.read(24)
        while True:
            rec_hdr = f.read(16)
            if len(rec_hdr) < 16: break
            ts_sec, ts_usec, incl_len, _ = struct.unpack("<IIII", rec_hdr)
            data = f.read(incl_len)
            if len(data) < incl_len: break
            yield ts_sec, ts_usec, data


def main(path, max_per_dev=4):
    samples = defaultdict(list)
    for ts_sec, ts_usec, data in parse_pcap(path):
        if len(data) < 27: continue
        hdr_len = struct.unpack("<H", data[0:2])[0]
        if hdr_len > len(data): continue
        bus = struct.unpack("<H", data[17:19])[0]
        dev = struct.unpack("<H", data[19:21])[0]
        ep = data[21]; transfer = data[22]
        payload = data[hdr_len:]
        if transfer != 1: continue           # interrupt only
        if (ep & 0x80) == 0: continue        # IN only
        if not payload: continue
        key = (bus, dev, ep)
        if len(samples[key]) < max_per_dev:
            samples[key].append(payload)

    print(f"Capture: {path}")
    for (b, d, ep), payloads in sorted(samples.items()):
        print(f"\n  bus={b} dev={d} ep=0x{ep:02x}: {len(payloads)} samples")
        for i, p in enumerate(payloads):
            print(f"    [{i}] len={len(p)}: {p[:40].hex(' ')}{' ...' if len(p) > 40 else ''}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"=== {p} ===")
        main(p)
