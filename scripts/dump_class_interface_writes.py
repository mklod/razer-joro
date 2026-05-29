"""Dump every CLASS_INTERFACE OUT control write from a USBPcap capture, including raw bytes."""
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


def main(path):
    devices = defaultdict(int)
    setup_irps = {}
    out_writes = []
    sig_seen = set()

    for ts_sec, ts_usec, data in parse_pcap(path):
        if len(data) < 27: continue
        hdr_len = struct.unpack("<H", data[0:2])[0]
        if hdr_len > len(data) or hdr_len < 27: continue
        irp = struct.unpack("<Q", data[2:10])[0]
        function = struct.unpack("<H", data[14:16])[0]
        info = data[16]
        bus = struct.unpack("<H", data[17:19])[0]
        device = struct.unpack("<H", data[19:21])[0]
        endpoint = data[21]
        transfer = data[22]
        extra = data[27:hdr_len]
        payload = data[hdr_len:]

        devices[(bus, device)] += 1

        # CLASS_INTERFACE (0x001B) + CONTROL_TRANSFER (0x0008): both are control requests.
        # Both have a 1-byte stage prefix in extra: 0=SETUP/start, 1=COMPLETE
        if transfer != 2:
            continue
        if not extra:
            continue
        stage = extra[0]
        if stage == 0:  # SETUP/start: extra[1..9] should be bmReq/bReq/wValue/wIndex/wLength
            if len(extra) < 9:
                # Print the structure to see what we have
                if (bus, device, function) not in sig_seen:
                    sig_seen.add((bus, device, function))
                    print(f"  short SETUP extra ({len(extra)} bytes) bus={bus} dev={device} fn=0x{function:04x}: {extra.hex(' ')}")
                continue
            bm = extra[1]; br = extra[2]
            wv = struct.unpack("<H", extra[3:5])[0]
            wi = struct.unpack("<H", extra[5:7])[0]
            wl = struct.unpack("<H", extra[7:9])[0]
            is_out = (bm & 0x80) == 0
            setup_irps[irp] = {
                'ts': (ts_sec, ts_usec), 'function': function,
                'bus': bus, 'dev': device,
                'bm': bm, 'br': br, 'wValue': wv, 'wIndex': wi, 'wLength': wl,
                'payload': payload,
                'is_out': is_out,
            }
            if is_out and payload:
                out_writes.append(setup_irps[irp])

    print(f"Devices: {dict(sorted(devices.items()))}")
    print(f"\nTotal OUT writes captured (with payload): {len(out_writes)}")

    # Group by (bus, dev, wIndex)
    by_target = defaultdict(list)
    for w in out_writes:
        by_target[(w['bus'], w['dev'], w['wIndex'])].append(w)
    print(f"\n  by target (bus, dev, interface_idx):")
    for k, ws in sorted(by_target.items()):
        b, d, i = k
        sizes = defaultdict(int)
        for w in ws:
            sizes[len(w['payload'])] += 1
        print(f"    bus={b} dev={d} wIndex=0x{i:04x}: {len(ws)} writes; sizes: {dict(sizes)}")

    # Show all distinct payload sizes
    sizes_total = defaultdict(int)
    for w in out_writes:
        sizes_total[len(w['payload'])] += 1
    print(f"\n  by payload size:")
    for l, n in sorted(sizes_total.items()):
        print(f"    len={l}: {n}")

    # Print first 10 writes verbose
    print(f"\nFirst 12 OUT writes:")
    for i, w in enumerate(out_writes[:12]):
        p = w['payload']
        ts_label = f"{w['ts'][0]}.{w['ts'][1]:06d}"
        print(f"  [{i:>2}] ts={ts_label} fn=0x{w['function']:04x} bus={w['bus']} dev={w['dev']} "
              f"bm=0x{w['bm']:02x} br=0x{w['br']:02x} wValue=0x{w['wValue']:04x} wIndex=0x{w['wIndex']:04x} "
              f"wLen={w['wLength']} payload_len={len(p)}")
        if len(p) >= 8:
            print(f"       proto30? status={p[0]:02x} trans={p[1]:02x} remain={p[2]:02x}{p[3]:02x} "
                  f"proto={p[4]:02x} dsize={p[5]} class=0x{p[6]:02x} cmd=0x{p[7]:02x}")
        print(f"       hex: {p[:48].hex(' ')}{' ...' if len(p) > 48 else ''}")


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(f"=== {p} ===")
        main(p)
