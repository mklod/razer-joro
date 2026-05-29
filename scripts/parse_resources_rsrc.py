"""
Parse .NET Resource Manager (.resources) format.
Magic: 0xBEEFCACE
Header includes count of resources, name list, and data blob.
"""
import struct, os

PATH = r'L:\PROJECTS\razer-joro\captures\fwu_extract\dotnet_resources\CustomerFWU2Point5.Properties.Resources.resources'
OUT = r'L:\PROJECTS\razer-joro\captures\fwu_extract\dotnet_resources\Properties_extract'
os.makedirs(OUT, exist_ok=True)

data = open(PATH, 'rb').read()
print(f"Resource file size: {len(data)} bytes")

# Magic check
magic = struct.unpack('<I', data[0:4])[0]
print(f"Magic: 0x{magic:08x} (expected 0xBEEFCACE)")
if magic != 0xBEEFCACE:
    print("Not a .NET resources file")
    raise SystemExit

# Resource Manager header version
ver1 = struct.unpack('<I', data[4:8])[0]
hdr_size = struct.unpack('<I', data[8:12])[0]
print(f"Version: {ver1}, header_size: {hdr_size}")

# Reader type string + version (encoded length-prefixed)
def read_7bit_int(buf, off):
    val = 0
    shift = 0
    while True:
        b = buf[off]
        off += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return val, off

def read_lp_string(buf, off):
    length, off = read_7bit_int(buf, off)
    s = buf[off:off+length].decode('utf-8', errors='replace')
    return s, off + length

off = 12
type_name, off = read_lp_string(data, off)
print(f"ResourceReader: {type_name}")

# Skip resource set type name
ver_bin, off = read_lp_string(data, off)
print(f"ResourceSet: {ver_bin[:80]}")

# Read RuntimeResourceReader version
rver = struct.unpack('<I', data[off:off+4])[0]
off += 4
print(f"Runtime resource reader version: {rver}")

num_resources = struct.unpack('<i', data[off:off+4])[0]
off += 4
print(f"Number of resources: {num_resources}")

num_types = struct.unpack('<i', data[off:off+4])[0]
off += 4
print(f"Number of types: {num_types}")

types = []
for i in range(num_types):
    t, off = read_lp_string(data, off)
    types.append(t)
    if i < 10:
        print(f"  type[{i}]: {t}")

# Pad to 8-byte alignment for the name hashes
while off & 7:
    off += 1

# Hash table (num_resources * 4 bytes)
print(f"Hashes section starts at 0x{off:x}")
off += num_resources * 4

# Name positions (num_resources * 4 bytes)
name_positions = struct.unpack(f'<{num_resources}i', data[off:off + num_resources*4])
off += num_resources * 4

data_section_off = struct.unpack('<I', data[off:off+4])[0]
off += 4
print(f"Data section starts at file offset: 0x{data_section_off:x}")

# Names section starts here
names_section = off
print(f"Names section starts at file offset: 0x{names_section:x}")

# Each name: 7-bit-length-prefixed UTF-16 string + 4-byte data offset
print(f"\nResources:")
entries = []
for np in name_positions:
    cur = names_section + np
    name_len, cur = read_7bit_int(data, cur)
    name = data[cur:cur+name_len].decode('utf-16le', errors='replace')
    cur += name_len
    data_off = struct.unpack('<i', data[cur:cur+4])[0]
    entries.append((name, data_off))

# Each entry's resource data is at data_section_off + data_off, prefixed
# with type code (7-bit int), then bytes (length-prefixed depending on type)
for name, doff in entries:
    abs_off = data_section_off + doff
    type_idx, after_type = read_7bit_int(data, abs_off)
    # For byte arrays type code = 32 (ResourceTypeCode.ByteArray) or
    # higher (UserType + N indexes the types array). Try byte array.
    # Read length-prefixed bytes for ByteArray.
    if type_idx == 32:  # ByteArray
        size = struct.unpack('<i', data[after_type:after_type+4])[0]
        body = data[after_type+4 : after_type+4+size]
        out_path = os.path.join(OUT, name + '.bin')
        with open(out_path, 'wb') as f:
            f.write(body)
        head = body[:16].hex()
        print(f"  {name:40s} ByteArray size={size:>10d} head=[{head}] -> {out_path}")
    elif type_idx == 1:  # String
        s_len, s_off = read_7bit_int(data, after_type)
        s = data[s_off:s_off+s_len].decode('utf-8', errors='replace')[:60]
        print(f"  {name:40s} String '{s}'")
    else:
        # Generic: try to dump first 16 bytes
        head = data[after_type:after_type+16].hex()
        print(f"  {name:40s} type_idx={type_idx} head=[{head}]")
