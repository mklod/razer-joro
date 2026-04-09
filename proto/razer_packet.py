# proto/razer_packet.py
# Last modified: 2026-04-09--1530
"""
Razer HID packet builder/parser.

Uses the openrazer struct layout (razer_report):
  [0x00] status          = 0x00 (new), 0x02 (ok), 0x03 (error), 0x05 (not supported)
  [0x01] transaction_id  = 0x1F (Joro)
  [0x02] remaining_packets_hi
  [0x03] remaining_packets_lo
  [0x04] protocol_type   = 0x00
  [0x05] data_size
  [0x06] command_class
  [0x07] command_id
  [0x08..0x57] arguments (80 bytes)
  [0x58] crc             = XOR of bytes [0x02..0x57]
  [0x59] reserved        = 0x00

Transport: USB control transfers via pyusb (NOT hidapi feature reports).
  SET_REPORT: bmRequestType=0x21, bRequest=0x09, wValue=0x0300, wIndex=0x03
  GET_REPORT: bmRequestType=0xA1, bRequest=0x01, wValue=0x0300, wIndex=0x03
"""

PACKET_SIZE = 90
TRANSACTION_ID = 0x1F

# LED constants
VARSTORE = 0x01
BACKLIGHT_LED = 0x05

# Status codes
STATUS_NEW = 0x00
STATUS_BUSY = 0x01
STATUS_OK = 0x02
STATUS_FAIL = 0x03
STATUS_TIMEOUT = 0x04
STATUS_NOT_SUPPORTED = 0x05


def _crc(buf: bytes) -> int:
    """XOR of bytes 2 through 87 (indices 0x02..0x57)."""
    result = 0
    for b in buf[2:88]:
        result ^= b
    return result


def build_packet(command_class: int, command_id: int, data_size: int, args: bytes = b"") -> bytes:
    """Build a 90-byte Razer HID packet (openrazer struct layout)."""
    buf = bytearray(PACKET_SIZE)
    buf[0x00] = STATUS_NEW
    buf[0x01] = TRANSACTION_ID
    # bytes 2-3: remaining_packets = 0
    # byte 4: protocol_type = 0
    buf[0x05] = data_size & 0xFF
    buf[0x06] = command_class
    buf[0x07] = command_id
    for i, b in enumerate(args[:80]):
        buf[0x08 + i] = b
    buf[0x58] = _crc(buf)
    buf[0x59] = 0x00
    return bytes(buf)


def parse_packet(buf: bytes) -> dict:
    """Parse a 90-byte Razer HID packet into fields."""
    if len(buf) < PACKET_SIZE:
        raise ValueError(f"Packet too short: {len(buf)} bytes, expected {PACKET_SIZE}")
    return {
        "status": buf[0x00],
        "transaction_id": buf[0x01],
        "remaining_packets": (buf[0x02] << 8) | buf[0x03],
        "protocol_type": buf[0x04],
        "data_size": buf[0x05],
        "command_class": buf[0x06],
        "command_id": buf[0x07],
        "args": buf[0x08:0x08 + buf[0x05]],
        "crc": buf[0x58],
        "crc_valid": buf[0x58] == _crc(buf),
    }


def format_packet(buf: bytes) -> str:
    """Pretty-print a packet for debugging."""
    p = parse_packet(buf)
    args_hex = " ".join(f"{b:02X}" for b in p["args"])
    status_names = {0: "NEW", 1: "BUSY", 2: "OK", 3: "FAIL", 4: "TIMEOUT", 5: "NOT_SUPPORTED"}
    status_name = status_names.get(p["status"], "???")
    return (
        f"status=0x{p['status']:02X}({status_name}) txn=0x{p['transaction_id']:02X} "
        f"class=0x{p['command_class']:02X} cmd=0x{p['command_id']:02X} "
        f"size={p['data_size']} crc_ok={p['crc_valid']}\n"
        f"  args: {args_hex}"
    )
