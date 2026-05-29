"""
Extract DEVICE->HOST Protocol30 responses. USBPcap stores HID class
control transfers as two events: SETUP submitted, then DATA returned.
For GET_REPORT (bmReq=0xA1, bReq=0x01) the data comes back from the
device. We want to pair each H->D SET_REPORT command with the D->H
GET_REPORT response that follows within ~50ms.

Razer protocol convention (most devices): host sends SET_REPORT, then
polls with GET_REPORT until the device sets status byte 0x02 = OK,
0x01 = busy, 0x05 = NOT_SUPPORTED. The response payload echoes the
original frame format with the device's results in args.
"""
import struct, collections
from scapy.all import rdpcap

PCAP = r'L:\PROJECTS\razer-joro\captures\dongle_setup_full_u1.pcap'
pk = rdpcap(PCAP)

# USBPcap header layout (variable, but consistent within a capture):
# bytes 0-1: hlen, 2-5: IRP id, 6-9: status, 10-11: func, 12: info,
# 13-14: bus, 15-16: dev, 17: endpoint, 18: transfer, 19-22: dataLen,
# then optional control-transfer-extra bytes ending in a SETUP packet.
# info byte at offset 12: bit 0 = direction (0=PDO->FDO=in, 1=FDO->PDO=out)
# Actually USBPcap docs: info bit 0 = 1 means PDO->FDO (FROM device).
#
# Simpler: scan for bmRequestType byte (0x21/0xA1) at the expected setup
# offset, AND check the URB function (transfer type).

want_classes = {0x00, 0x0b, 0x04, 0x05, 0x06, 0x02, 0x0f}
results = []
for pi, p in enumerate(pk):
    raw = bytes(p)
    if len(raw) < 29: continue
    hlen = struct.unpack('<H', raw[0:2])[0]
    if hlen + 8 > len(raw): continue
    # Try BOTH setup-signature variants and BOTH offsets
    for o in range(max(0, hlen-4), min(hlen+10, len(raw)-8)):
        bm, br = raw[o], raw[o+1]
        if br != 0x09 and br != 0x01: continue
        if bm != 0x21 and bm != 0xA1: continue
        wLen = struct.unpack('<H', raw[o+6:o+8])[0]
        if wLen != 90: continue
        # Payload may follow the setup (host->dev SET) or come in a separate
        # later packet (dev->host GET response data).
        payload_off = o + 8
        if payload_off + 90 <= len(raw):
            rz = raw[payload_off:payload_off+90]
            h2d = (bm == 0x21)
            cls, cmd = rz[6], rz[7]
            status = rz[0]
            results.append((pi, h2d, status, cls, cmd, rz[5], bytes(rz[8:88])))
        else:
            # GET_REPORT with no inline data — the data comes in a follow-up
            # packet. Mark this as a GET request marker.
            results.append((pi, False, 0xFF, 0xFF, 0xFF, 0, b''))
        break

# Some pcaps have the URB data on a separate packet (data-stage in/out
# after the setup). Catch payload-only packets: 90-byte payload that
# starts with a status byte in {0x00,0x01,0x02,0x03,0x04,0x05} followed
# by a plausible txid + remaining=0.
print(f"setup-anchored parses: {len(results)}")
# Also rescan ALL packets for stand-alone 90-byte payloads
standalones = []
for pi, p in enumerate(pk):
    raw = bytes(p)
    # search for a 90-byte Protocol30-shaped blob anywhere
    for o in range(len(raw) - 90):
        win = raw[o:o+90]
        st = win[0]
        if st not in (0x00, 0x01, 0x02, 0x03, 0x04, 0x05): continue
        rem = struct.unpack('>H', win[2:4])[0]
        if rem != 0: continue
        if win[4] != 0x1f: continue  # proto byte
        cls, cmd, ds = win[6], win[7], win[5]
        if cls > 0x10 or ds > 80: continue
        # verify XOR checksum
        x = 0
        for b in win[2:88]: x ^= b
        if x != win[88]: continue
        standalones.append((pi, o, st, cls, cmd, ds, bytes(win[8:88])))
        break

print(f"standalone 90B Protocol30 blobs found: {len(standalones)}")

# Cross-correlate: a SET_REPORT at packet N is usually followed within
# 1-5 packets by a GET_REPORT response (same class:cmd, different status).
# Build the response-pair index by checking standalones in temporal order.
print("\n=== Pair-window standalone responses (status != 0x00) ===")
hits = 0
for pi, off, st, cls, cmd, ds, a in standalones:
    if hits >= 60: break
    if st == 0x00: continue  # status=0 is new request, not a response
    # status: 0x01=busy, 0x02=ok, 0x03=fail, 0x04=timeout, 0x05=notsup
    nz = len(a.rstrip(b'\x00'))
    if cls in want_classes:
        print(f"  pkt#{pi:5d} off={off:3d} status={st:#04x} {cls:02x}:{cmd:02x} ds={ds:2d} nz={nz:2d}B args={a[:max(ds,nz,1)].hex()}")
        hits += 1

# Specifically look for responses to the 0xda question — any standalone
# with class=0x00, cmd=0x41 (or 0xc1 = response counterpart)
print("\n=== 00:41 + responses (0xda hunt) ===")
for pi, off, st, cls, cmd, ds, a in standalones:
    if cls == 0x00 and cmd in (0x41, 0xc1):
        print(f"  pkt#{pi:5d} status={st:#04x} {cls:02x}:{cmd:02x} ds={ds} args={a[:max(ds,1)].hex()}")

# Look for 0xda in any standalone payload (broader scan)
print("\n=== 0xda in any Protocol30 blob ===")
for pi, off, st, cls, cmd, ds, a in standalones:
    if 0xda in a[:max(ds, 8)]:
        positions = [i for i, b in enumerate(a[:max(ds, 16)]) if b == 0xda]
        print(f"  pkt#{pi:5d} status={st:#04x} {cls:02x}:{cmd:02x} ds={ds} 0xda@{positions} args={a[:24].hex()}")

# Look for any response that contains a recognizable "Joro" device id —
# what does the dongle KNOW about the keyboard? Look at 00:c5 (device
# enumerate) responses.
print("\n=== 00:c5 / 00:86 / 00:bf response payloads (device enum) ===")
for pi, off, st, cls, cmd, ds, a in standalones:
    if cls == 0x00 and cmd in (0xc5, 0x86, 0x46, 0xbf, 0x45, 0xc6):
        nz = len(a.rstrip(b'\x00'))
        if nz > 0 and st != 0x00:
            print(f"  pkt#{pi:5d} status={st:#04x} {cls:02x}:{cmd:02x} ds={ds} nz={nz} args={a[:max(nz,8)].hex()}")
