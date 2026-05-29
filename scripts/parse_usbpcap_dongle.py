"""
parse_usbpcap_dongle.py — find any host->device traffic to the dongle and
identify Razer-shaped payloads (90-byte typical Protocol30, or other framings).

USBPcap link type 249.
"""
import struct
import sys
from collections import defaultdict


def parse_pcap(path):
    with open(path, "rb") as f:
        global_hdr = f.read(24)
        if len(global_hdr) < 24:
            return
        magic = struct.unpack("<I", global_hdr[:4])[0]
        if magic not in (0xa1b2c3d4, 0xd4c3b2a1):
            return
        while True:
            rec_hdr = f.read(16)
            if len(rec_hdr) < 16:
                break
            ts_sec, ts_usec, incl_len, orig_len = struct.unpack("<IIII", rec_hdr)
            data = f.read(incl_len)
            if len(data) < incl_len:
                break
            yield ts_sec, ts_usec, data


def parse_usbpcap_packet(data):
    if len(data) < 27:
        return None
    hdr_len = struct.unpack("<H", data[0:2])[0]
    if hdr_len > len(data) or hdr_len < 27:
        return None
    irp_id = struct.unpack("<Q", data[2:10])[0]
    status = struct.unpack("<I", data[10:14])[0]
    function = struct.unpack("<H", data[14:16])[0]
    info = data[16]                                  # bit0: 0=PDO, 1=FDO; bit7: source
    bus = struct.unpack("<H", data[17:19])[0]
    device = struct.unpack("<H", data[19:21])[0]
    endpoint = data[21]                              # bit7: 0=OUT, 1=IN
    transfer = data[22]                              # 0=ISO 1=INT 2=CTL 3=BULK
    data_length = struct.unpack("<I", data[23:27])[0]
    return {
        'irp_id': irp_id, 'status': status, 'function': function, 'info': info,
        'bus': bus, 'device': device, 'endpoint': endpoint, 'transfer': transfer,
        'data_length': data_length,
        'header_extra': data[27:hdr_len],
        'payload': data[hdr_len:],
    }


TRANSFER_NAMES = {0: "ISO", 1: "INT", 2: "CTL", 3: "BULK"}


def main(paths, target_bus=None, target_device=None):
    by_dev = defaultdict(int)
    out_lengths = defaultdict(lambda: defaultdict(int))     # (bus,dev,transfer) -> {len: n}
    razer_writes = []                                       # entries with 90-byte payload
    ctl_setups_by_irp = {}                                  # irpId -> setup dict
    interrupt_out_writes = []
    bulk_out_writes = []

    for path in paths:
        for _ts, _us, data in parse_pcap(path):
            pkt = parse_usbpcap_packet(data)
            if pkt is None:
                continue
            by_dev[(pkt['bus'], pkt['device'])] += 1

            if target_bus is not None and pkt['bus'] != target_bus:
                continue
            if target_device is not None and pkt['device'] != target_device:
                continue

            ep = pkt['endpoint']
            is_out = (ep & 0x80) == 0  # OUT endpoint
            xtype = pkt['transfer']

            # Track setups for control transfers (URB_FUNCTION_CLASS/VENDOR/STD)
            extra = pkt['header_extra']
            if xtype == 2 and len(extra) >= 9 and extra[0] == 0:  # SETUP stage
                bm = extra[1]; br = extra[2]
                wValue = struct.unpack("<H", extra[3:5])[0]
                wIndex = struct.unpack("<H", extra[5:7])[0]
                wLength = struct.unpack("<H", extra[7:9])[0]
                # OUT direction has bit 7 of bmRequestType = 0
                ctl_setups_by_irp[pkt['irp_id']] = {
                    'bm': bm, 'br': br, 'wValue': wValue, 'wIndex': wIndex,
                    'wLength': wLength,
                    'payload': pkt['payload'],
                    'bus': pkt['bus'], 'dev': pkt['device'],
                }
                if (bm & 0x80) == 0 and pkt['payload']:
                    out_lengths[(pkt['bus'], pkt['device'], 'CTL')][len(pkt['payload'])] += 1
                    if len(pkt['payload']) == 90:
                        razer_writes.append(('CTL', pkt['bus'], pkt['device'], wIndex, wValue, bm, br, pkt['payload']))

            # DATA stage of control transfer (sometimes data comes here)
            elif xtype == 2 and len(extra) >= 1 and extra[0] == 1 and is_out:
                if pkt['irp_id'] in ctl_setups_by_irp:
                    s = ctl_setups_by_irp[pkt['irp_id']]
                    if not s['payload']:
                        s['payload'] = pkt['payload']
                        out_lengths[(s['bus'], s['dev'], 'CTL')][len(pkt['payload'])] += 1
                        if len(pkt['payload']) == 90:
                            razer_writes.append(('CTL-d', s['bus'], s['dev'], s['wIndex'], s['wValue'], s['bm'], s['br'], pkt['payload']))

            # Interrupt OUT
            if xtype == 1 and is_out and pkt['payload']:
                out_lengths[(pkt['bus'], pkt['device'], 'INT')][len(pkt['payload'])] += 1
                interrupt_out_writes.append((pkt['bus'], pkt['device'], ep, pkt['payload']))
                if len(pkt['payload']) == 90:
                    razer_writes.append(('INT', pkt['bus'], pkt['device'], 0, ep, 0, 0, pkt['payload']))
            # Bulk OUT
            if xtype == 3 and is_out and pkt['payload']:
                out_lengths[(pkt['bus'], pkt['device'], 'BULK')][len(pkt['payload'])] += 1
                bulk_out_writes.append((pkt['bus'], pkt['device'], ep, pkt['payload']))
                if len(pkt['payload']) == 90:
                    razer_writes.append(('BULK', pkt['bus'], pkt['device'], 0, ep, 0, 0, pkt['payload']))

    print("Devices observed (bus, dev) -> packet count:")
    for (b, d), n in sorted(by_dev.items(), key=lambda x: -x[1]):
        print(f"  bus={b} dev={d}: {n} packets")

    print("\nOUT-direction payload size histogram per (bus,dev,xtype):")
    for key, hist in sorted(out_lengths.items()):
        print(f"  {key}:")
        for l, n in sorted(hist.items()):
            print(f"    len={l}: {n}")

    print(f"\nTotal interrupt-OUT writes: {len(interrupt_out_writes)}")
    print(f"Total bulk-OUT writes: {len(bulk_out_writes)}")

    # Show every distinct CTL setup (bm/br/wValue/wIndex combo) and count
    setups = defaultdict(int)
    for s in ctl_setups_by_irp.values():
        if (s['bm'] & 0x80) == 0:
            setups[(s['bus'], s['dev'], s['bm'], s['br'], s['wValue'], s['wIndex'], s['wLength'])] += 1
    if setups:
        print("\nDistinct OUT control transfers (bus,dev,bmReq,bReq,wValue,wIndex,wLength) -> count:")
        for k, n in sorted(setups.items(), key=lambda x: -x[1]):
            b, d, bm, br, wv, wi, wl = k
            print(f"  bus={b} dev={d} bm=0x{bm:02x} br=0x{br:02x} wValue=0x{wv:04x} wIndex=0x{wi:04x} wLen={wl}: {n}")

    # Show 90-byte payloads
    if razer_writes:
        print(f"\n=== {len(razer_writes)} ninety-byte payloads (Razer Protocol30 candidates) ===")
        # Print a few unique
        seen = set()
        unique = []
        for w in razer_writes:
            sig = bytes(w[7][:16])  # first 16 bytes signature
            if sig not in seen:
                seen.add(sig)
                unique.append(w)
        print(f"  unique by first-16-bytes: {len(unique)}, showing first 12:")
        for i, (xtype, b, d, wi, wv, bm, br, p) in enumerate(unique[:12]):
            print(f"\n  --- {xtype} bus={b} dev={d} wIndex=0x{wi:04x} wValue=0x{wv:04x} bm=0x{bm:02x} br=0x{br:02x}")
            if len(p) >= 90:
                print(f"    status={p[0]:02x} trans_id={p[1]:02x} remain={p[2]:02x}{p[3]:02x} "
                      f"proto={p[4]:02x} dsize={p[5]:02d} class=0x{p[6]:02x} cmd=0x{p[7]:02x}")
                print(f"    args[0..{p[5]}]: {p[8:8+min(p[5],20)].hex(' ')}")
            print(f"    hex(0..32): {p[:32].hex(' ')}")
            print(f"    hex(32..64): {p[32:64].hex(' ')}")
            print(f"    hex(64..90): {p[64:90].hex(' ')}")
    else:
        print("\nNo 90-byte writes found. Synapse is using a different framing.")
        # Show top-N OUT payload sizes
        all_out_sizes = defaultdict(int)
        for hist in out_lengths.values():
            for l, n in hist.items():
                all_out_sizes[l] += n
        print("Top OUT payload sizes overall:")
        for l, n in sorted(all_out_sizes.items(), key=lambda x: -x[1])[:15]:
            print(f"  len={l}: {n} writes")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    flags = [a for a in sys.argv[1:] if a.startswith('--')]
    target_bus = target_device = None
    for f in flags:
        if f.startswith('--bus='):
            target_bus = int(f.split('=')[1])
        if f.startswith('--device='):
            target_device = int(f.split('=')[1])
    main(args, target_bus, target_device)
