"""Extract all 90-byte Razer Protocol30 packets from a USBPcap capture
and print each one's class/cmd/dsize/args. Filters to USB control
transfers on the Joro dongle (PID 0x009C).

Razer Protocol30 layout (90 bytes):
  [0]   status (0x00 = new)
  [1]   transaction_id
  [2-3] remaining_packets
  [4]   protocol_type
  [5]   data_size
  [6]   command_class
  [7]   command_id
  [8-87] args (80 bytes)
  [88]  CRC (XOR of bytes 2..88)
  [89]  trailing
"""
import sys
from struct import unpack
try:
    from scapy.all import rdpcap
except ImportError:
    print("Need: pip install scapy", file=sys.stderr); sys.exit(1)

PCAP = sys.argv[1]
pkts = rdpcap(PCAP)
print(f"Loaded {len(pkts)} packets from {PCAP}")

# USBPcap frames: parse manually (scapy USB layer is unreliable on USBPcap pcaps)
# Frame structure: USBPcap header (27B) + URB structure + setup (if control) + data

found = 0
for i, p in enumerate(pkts):
    raw = bytes(p)
    if len(raw) < 27 + 8 + 90:
        continue  # too small to contain a Razer packet
    # USBPcap header has variable length; the URB info structure begins at offset 14.
    # Easier: search for the SETUP signature for class SET_REPORT: bmReq=0x21 bReq=0x09
    setup_off = None
    for off in range(20, min(40, len(raw) - 8)):
        if raw[off] == 0x21 and raw[off+1] == 0x09:
            setup_off = off
            break
    if setup_off is None:
        continue
    # SETUP (8B): bmReq bReq wValue(LE) wIndex(LE) wLength(LE)
    bmReq, bReq = raw[setup_off], raw[setup_off+1]
    wValue, = unpack('<H', raw[setup_off+2:setup_off+4])
    wIndex, = unpack('<H', raw[setup_off+4:setup_off+6])
    wLength, = unpack('<H', raw[setup_off+6:setup_off+8])
    if wLength != 90:
        continue
    razer = raw[setup_off+8:setup_off+8+90]
    if len(razer) != 90:
        continue
    # Skip ACK/echo packets where everything is zero
    cclass, ccmd = razer[6], razer[7]
    dsize = razer[5]
    txid = razer[1]
    args = razer[8:8+min(dsize, 80)]  # show full args
    args_hex = ' '.join(f'{b:02x}' for b in args)
    print(f"frame={i+1}  iface={wIndex}  txid=0x{txid:02x}  class=0x{cclass:02x}  cmd=0x{ccmd:02x}  dsize={dsize}  args=[{args_hex}]")
    found += 1

print(f"\nTotal Razer SET_REPORT packets: {found}")
