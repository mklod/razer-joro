"""Dump every URB type/direction breakdown for a USBPcap capture, focused on a target device."""
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
            yield data


def main(path, bus_filter=None, dev_filter=None):
    counts = defaultdict(int)  # (xtype, direction, has_payload) -> count
    setups = defaultdict(int)
    sample_by_function = defaultdict(int)
    func_dirs = defaultdict(int)

    for data in parse_pcap(path):
        if len(data) < 27: continue
        hdr_len = struct.unpack("<H", data[0:2])[0]
        if hdr_len > len(data) or hdr_len < 27: continue
        function = struct.unpack("<H", data[14:16])[0]
        info = data[16]
        bus = struct.unpack("<H", data[17:19])[0]
        device = struct.unpack("<H", data[19:21])[0]
        endpoint = data[21]
        transfer = data[22]
        payload = data[hdr_len:]
        extra = data[27:hdr_len]

        if bus_filter is not None and bus != bus_filter: continue
        if dev_filter is not None and device != dev_filter: continue

        sample_by_function[(transfer, function)] += 1
        is_in = (endpoint & 0x80) != 0
        direction = 'IN' if is_in else 'OUT'
        # info bit0: 0=PDO (going down/up to driver?), bit7: source PID
        counts[(transfer, direction, len(payload) > 0)] += 1
        func_dirs[(transfer, function, direction, len(payload))] += 1

        if transfer == 2 and len(extra) >= 9 and extra[0] == 0:
            bm = extra[1]; br = extra[2]
            wv = struct.unpack("<H", extra[3:5])[0]
            wi = struct.unpack("<H", extra[5:7])[0]
            wl = struct.unpack("<H", extra[7:9])[0]
            setups[(bm, br, wv, wi, wl)] += 1

    print(f"\n(transfer, direction, has_payload) -> count:")
    for k, n in sorted(counts.items()):
        print(f"  transfer={k[0]} dir={k[1]} payload>0={k[2]}: {n}")

    print(f"\n(transfer, urb_function) -> count   [function names: 0x08=CTL_TRANSFER, 0x09=BULK/INT, 0x0B=ISOCH, 0x1A=CLASS_DEVICE, 0x1B=CLASS_INTERFACE, ...]:")
    for k, n in sorted(sample_by_function.items(), key=lambda x: -x[1])[:20]:
        print(f"  transfer={k[0]} function=0x{k[1]:04x}: {n}")

    print(f"\nDistinct SETUP (bmReq, bReq, wValue, wIndex, wLength) -> count:")
    for k, n in sorted(setups.items(), key=lambda x: -x[1])[:30]:
        bm, br, wv, wi, wl = k
        dir_label = 'IN' if bm & 0x80 else 'OUT'
        type_bits = (bm >> 5) & 3  # 0=std, 1=class, 2=vendor
        type_label = {0:'std', 1:'class', 2:'vendor', 3:'reserved'}[type_bits]
        print(f"  {dir_label} {type_label}  bm=0x{bm:02x} br=0x{br:02x} wValue=0x{wv:04x} wIndex=0x{wi:04x} wLen={wl}: {n}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    bus = dev = None
    for f in flags:
        if f.startswith('--bus='): bus = int(f.split('=')[1])
        if f.startswith('--device='): dev = int(f.split('=')[1])
    for p in args:
        print(f"=== {p} ===")
        main(p, bus, dev)
