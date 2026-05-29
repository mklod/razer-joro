"""Extract all CLASS_INTERFACE OUT writes from a USBPcap capture, dumping
the full 90-byte Razer Protocol30 packet for each. Tag any that look like
a set-color (class=0x0F cmd=0x02 OR cmd=0x03 with non-zero RGB args).
"""
import struct, sys


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


def main(path):
    writes = []
    for ts_sec, ts_usec, data in parse_pcap(path):
        if len(data) < 27: continue
        hdr_len = struct.unpack("<H", data[0:2])[0]
        function = struct.unpack("<H", data[14:16])[0]
        bus = struct.unpack("<H", data[17:19])[0]
        dev = struct.unpack("<H", data[19:21])[0]
        endpoint = data[21]
        transfer = data[22]
        payload = data[hdr_len:]
        if function != 0x001B: continue
        if transfer != 2: continue
        if (endpoint & 0x80): continue
        if len(payload) < 98: continue
        # First 8 bytes are SETUP, then 90-byte Protocol30 packet
        setup = payload[:8]
        bm = setup[0]; br = setup[1]
        wValue = struct.unpack("<H", setup[2:4])[0]
        wIndex = struct.unpack("<H", setup[4:6])[0]
        wLength = struct.unpack("<H", setup[6:8])[0]
        if (bm & 0x80) != 0: continue  # only OUT
        pkt = payload[8:8+90]
        if len(pkt) != 90: continue
        writes.append((ts_sec, ts_usec, bus, dev, wIndex, pkt))

    print(f"Extracted {len(writes)} OUT writes\n")

    # Categorize by class/cmd
    from collections import defaultdict
    by_cmd = defaultdict(list)
    for w in writes:
        pkt = w[5]
        cls, cmd = pkt[6], pkt[7]
        by_cmd[(cls, cmd)].append(w)

    print("By (class, cmd):")
    for (cls, cmd), ws in sorted(by_cmd.items()):
        print(f"  class=0x{cls:02x} cmd=0x{cmd:02x}: {len(ws)} writes")

    # Show full hex of every distinct payload (by content signature)
    seen = set()
    print("\nDistinct packet contents:")
    for ts_sec, ts_usec, bus, dev, wi, pkt in writes:
        sig = bytes(pkt[5:24])  # data_size + class + cmd + args (non-noise area)
        if sig in seen: continue
        seen.add(sig)
        cls, cmd, ds = pkt[6], pkt[7], pkt[5]
        args_n = min(ds, 80)
        args = pkt[8:8+args_n]
        nonzero_args = any(a != 0 for a in args)
        marker = " [HAS-RGB?]" if nonzero_args and cls == 0x0f else ""
        print(f"\n  ts={ts_sec}.{ts_usec:06d} bus={bus} dev={dev} wIndex=0x{wi:04x}{marker}")
        print(f"    status={pkt[0]:02x} trans={pkt[1]:02x} remain={pkt[2]:02x}{pkt[3]:02x} "
              f"proto={pkt[4]:02x} dsize={ds:02d} class=0x{cls:02x} cmd=0x{cmd:02x}")
        print(f"    args[0..{args_n}]: {args.hex(' ')}")
        # Full 90-byte hex grid
        print(f"    raw: {pkt[:32].hex(' ')}")
        print(f"         {pkt[32:64].hex(' ')}")
        print(f"         {pkt[64:90].hex(' ')}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"=== {p} ===")
        main(p)
