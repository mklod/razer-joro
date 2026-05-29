"""Dump raw bytes of first N URB packets matching filter, to understand USBPcap layout for CLASS_INTERFACE."""
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
            yield data


def main(path, target_function=0x001B, max_dump=20):
    n = 0
    for data in parse_pcap(path):
        if len(data) < 27: continue
        hdr_len = struct.unpack("<H", data[0:2])[0]
        function = struct.unpack("<H", data[14:16])[0]
        if function != target_function:
            continue
        n += 1
        if n > max_dump: break
        info = data[16]
        bus = struct.unpack("<H", data[17:19])[0]
        dev = struct.unpack("<H", data[19:21])[0]
        ep = data[21]
        transfer = data[22]
        data_length = struct.unpack("<I", data[23:27])[0]
        extra = data[27:hdr_len]
        payload = data[hdr_len:]
        print(f"--- packet #{n}: total={len(data)} hdr_len={hdr_len} info=0x{info:02x} bus={bus} dev={dev} ep=0x{ep:02x} xfer={transfer} dataLen={data_length}")
        print(f"    extra ({len(extra)} bytes): {extra.hex(' ')}")
        print(f"    payload ({len(payload)} bytes): {payload[:64].hex(' ')}{' ...' if len(payload)>64 else ''}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    fn_target = 0x001B
    for f in flags:
        if f.startswith('--fn='): fn_target = int(f.split('=')[1], 0)
    for p in args:
        print(f"\n===== {p}  fn=0x{fn_target:04x} =====")
        main(p, fn_target)
