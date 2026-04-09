# proto/razer_packet.py
# Last modified: 2026-04-09--0000
"""
Razer HID packet builder/parser.

90-byte packet structure:
  [0x00] report_id      = 0x00
  [0x01] status          = 0x00 (new), 0x02 (ok), 0x03 (error)
  [0x02] transaction_id  = 0x1F (Joro)
  [0x03] data_size_hi
  [0x04] data_size_lo
  [0x05] command_class
  [0x06] command_id
  [0x07..0x57] arguments (80 bytes)
  [0x58] crc             = XOR of bytes [0x02..0x57]
  [0x59] reserved        = 0x00
"""

PACKET_SIZE = 90
TRANSACTION_ID = 0x1F


def _crc(buf: bytes) -> int:
    """XOR of bytes 2 through 87 (indices 0x02..0x57)."""
    result = 0
    for b in buf[2:88]:
        result ^= b
    return result


def build_packet(command_class: int, command_id: int, data_size: int, args: bytes = b"") -> bytes:
    """Build a 90-byte Razer HID packet."""
    buf = bytearray(PACKET_SIZE)
    buf[0x00] = 0x00  # report_id
    buf[0x01] = 0x00  # status: new command
    buf[0x02] = TRANSACTION_ID
    buf[0x03] = (data_size >> 8) & 0xFF
    buf[0x04] = data_size & 0xFF
    buf[0x05] = command_class
    buf[0x06] = command_id
    for i, b in enumerate(args[:80]):
        buf[0x07 + i] = b
    buf[0x58] = _crc(buf)
    buf[0x59] = 0x00
    return bytes(buf)


def parse_packet(buf: bytes) -> dict:
    """Parse a 90-byte Razer HID packet into fields."""
    if len(buf) < PACKET_SIZE:
        raise ValueError(f"Packet too short: {len(buf)} bytes, expected {PACKET_SIZE}")
    data_size = (buf[0x03] << 8) | buf[0x04]
    return {
        "report_id": buf[0x00],
        "status": buf[0x01],
        "transaction_id": buf[0x02],
        "data_size": data_size,
        "command_class": buf[0x05],
        "command_id": buf[0x06],
        "args": buf[0x07:0x07 + data_size],
        "crc": buf[0x58],
        "crc_valid": buf[0x58] == _crc(buf),
    }


def format_packet(buf: bytes) -> str:
    """Pretty-print a packet for debugging."""
    p = parse_packet(buf)
    args_hex = " ".join(f"{b:02X}" for b in p["args"])
    return (
        f"status=0x{p['status']:02X} txn=0x{p['transaction_id']:02X} "
        f"class=0x{p['command_class']:02X} cmd=0x{p['command_id']:02X} "
        f"size={p['data_size']} crc_ok={p['crc_valid']}\n"
        f"  args: {args_hex}"
    )
