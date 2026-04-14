#!/usr/bin/env python3
"""Parse USBPcap capture looking for Razer Protocol30 control transfers.

Finds 90-byte SET_REPORT control transfers to VID 1532 / PID 02CD and dumps
class=0x02 command packets in readable form. Flags the command_id so we can
spot base-layer remap writes, mode-switch writes, etc.
"""
import struct
import sys
from pathlib import Path

PCAP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "synapse_f4_u3.pcap"

# Razer command_id -> friendly name (best guesses from openrazer / prior captures)
CMD_NAMES = {
    0x0D: "set_layer_remap",
    0x0F: "set_keymap_entry",
    0x02: "set_led_effect",
    0x01: "set_led_state",
    0x03: "set_led_brightness",
    0x80: "get_battery",
    0x81: "get_charging",
    0x84: "get_firmware",
}

def parse_pcap(path: Path):
    data = path.read_bytes()
    # Global header: 24 bytes
    magic = struct.unpack_from("<I", data, 0)[0]
    if magic != 0xa1b2c3d4:
        print(f"unexpected magic {magic:#x}")
        return
    offset = 24
    n = 0
    while offset + 16 <= len(data):
        ts_s, ts_us, incl_len, orig_len = struct.unpack_from("<IIII", data, offset)
        offset += 16
        pkt = data[offset:offset + incl_len]
        offset += incl_len
        n += 1
        yield n, pkt

def parse_usbpcap(pkt: bytes):
    if len(pkt) < 2:
        return None
    header_len = struct.unpack_from("<H", pkt, 0)[0]
    if header_len > len(pkt):
        return None
    # USBPCAP_BUFFER_PACKET_HEADER layout:
    # u16 headerLen, u64 irpId, u32 status, u16 function, u8 info,
    # u16 bus, u16 device, u8 endpoint, u8 transfer, u32 dataLength
    fmt = "<HQIHBHHBBI"
    if header_len < struct.calcsize(fmt):
        return None
    header = struct.unpack_from(fmt, pkt, 0)
    (_hlen, _irp, _status, _func, info, bus, dev, endpoint, transfer, data_len) = header
    payload = pkt[header_len:]
    return {
        "info": info,
        "bus": bus,
        "dev": dev,
        "endpoint": endpoint,
        "transfer": transfer,
        "data_len": data_len,
        "payload": payload,
    }

def decode_razer_pkt(data: bytes):
    if len(data) < 90:
        return None
    status, trans_id, rem_hi, rem_lo, proto, dsize, cclass, cid = data[:8]
    args = data[8:88]
    crc = data[88]
    return {
        "status": status,
        "trans_id": trans_id,
        "data_size": dsize,
        "class": cclass,
        "cmd": cid,
        "args": args,
        "crc": crc,
    }

def hex_row(b: bytes, n: int = 16) -> str:
    return " ".join(f"{x:02x}" for x in b[:n])

def main():
    print(f"Parsing {PCAP} ({PCAP.stat().st_size:,} bytes)")
    razer_pkts = []
    for n, pkt in parse_pcap(PCAP):
        u = parse_usbpcap(pkt)
        if not u:
            continue
        # Transfer type 0x02 = CONTROL in USBPcap
        if u["transfer"] != 0x02:
            continue
        # Control transfers have an 8-byte setup packet at the head of payload
        payload = u["payload"]
        if len(payload) < 8:
            continue
        setup = payload[:8]
        bmRT, bReq, wVal, wIdx, wLen = struct.unpack("<BBHHH", setup)
        # Razer uses HID class requests: SET_REPORT (bReq=0x09) and
        # GET_REPORT (bReq=0x01) over wLen=0x5A (90).
        if bReq not in (0x09, 0x01) or wLen != 0x005a:
            continue
        data = payload[8:8 + 90]
        if len(data) < 90:
            # OUT phase of a control-read has no data; the data comes on the
            # IN phase in a separate URB. Skip the empty half.
            continue
        rp = decode_razer_pkt(data)
        if not rp:
            continue
        rp["bReq"] = bReq
        rp["bmRT"] = bmRT
        razer_pkts.append((n, u, rp, data))

    print(f"Found {len(razer_pkts)} Razer SET_REPORT packets")
    print()
    # Timeline of interesting writes (skip the brightness flood)
    print("=== Timeline of non-brightness WRITES (SET only) ===")
    for n, u, rp, raw in razer_pkts:
        if rp.get('bReq') != 0x09:
            continue
        # Skip brightness flood
        if rp['class'] == 0x0f and rp['cmd'] == 0x03:
            continue
        # Skip status-only/no-op probes by filtering class=0x00 get-* probes
        if rp['class'] == 0x00 and rp['cmd'] >= 0x80:
            continue
        hexa = hex_row(rp['args'], 10)
        print(f"  f={n:>5} cls=0x{rp['class']:02x} cmd=0x{rp['cmd']:02x} dsize={rp['data_size']:>2} args={hexa}")
    print()

    # Focus on class=0x02 (keyboard/keymap). Count by (class, cmd).
    from collections import Counter
    counts = Counter((p[2]["class"], p[2]["cmd"]) for p in razer_pkts)
    print("Class/cmd distribution:")
    for (cls, cmd), count in sorted(counts.items()):
        name = CMD_NAMES.get(cmd, "?")
        print(f"  class=0x{cls:02x} cmd=0x{cmd:02x} ({name:18s})  x{count}")
    print()

    # Dump class=0x02 keymap-related packets
    print("=== class=0x02 cmd=0x0d/0x0f packets ===")
    for n, u, rp, raw in razer_pkts:
        if rp["class"] != 0x02:
            continue
        if rp["cmd"] not in (0x0D, 0x0F):
            continue
        print(f"\nframe={n} cmd=0x{rp['cmd']:02x} dsize={rp['data_size']}")
        print(f"  args[0..16] = {hex_row(rp['args'], 16)}")

    # Dump any "exotic" writes we haven't seen before (all of them)
    print("\n=== class=0x02 other-cmd packets (possible mode switch) ===")
    for n, u, rp, raw in razer_pkts:
        if rp["class"] != 0x02:
            continue
        if rp["cmd"] in (0x0D, 0x0F):
            continue
        req = "SET" if rp['bReq'] == 0x09 else "GET"
        print(f"\nframe={n} {req} bmRT=0x{rp['bmRT']:02x} status=0x{rp['status']:02x} cmd=0x{rp['cmd']:02x} dsize={rp['data_size']}")
        print(f"  args[0..16] = {hex_row(rp['args'], 16)}")

    # Dump class=0x00 packets too (device-info / identity commands)
    print("\n=== class=0x00 packets ===")
    for n, u, rp, raw in razer_pkts:
        if rp["class"] != 0x00:
            continue
        print(f"\nframe={n} status=0x{rp['status']:02x} cmd=0x{rp['cmd']:02x} dsize={rp['data_size']}")
        print(f"  args[0..16] = {hex_row(rp['args'], 16)}")

    # Look for writes matching the right-ctrl matrix index 0x40 (set earlier)
    print("\n=== packets with 0x40 in args (Right Ctrl matrix) ===")
    for n, u, rp, raw in razer_pkts:
        if rp["class"] != 0x02:
            continue
        if 0x40 in rp["args"][:10]:
            print(f"frame={n} cmd=0x{rp['cmd']:02x} dsize={rp['data_size']} args={hex_row(rp['args'], 16)}")

if __name__ == "__main__":
    main()
