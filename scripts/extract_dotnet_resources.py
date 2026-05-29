"""
Extract .NET embedded resources from CustomerFWU2Point5.exe to disk.

The 'CustomerFWU2Point5.Resources.ResourceStr.resources' is 15.5 MB and
likely contains the encrypted firmware blob for each device the updater
supports (Joro, Talia, Hazel2, etc.). Each is keyed by name in a
.NET ResourceManager binary stream (.resources format).
"""
import os, struct, dnfile

PE_PATH = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents\CustomerFWU2Point5.exe'
OUT = r'L:\PROJECTS\razer-joro\captures\fwu_extract\dotnet_resources'
os.makedirs(OUT, exist_ok=True)

pe = dnfile.dnPE(PE_PATH)
pe.parse_data_directories()

# Find the resources directory's RVA + size
rsrc_rva = pe.net.struct.ResourcesRva
rsrc_size = pe.net.struct.ResourcesSize
rsrc_off = pe.get_offset_from_rva(rsrc_rva)
print(f"Resources base: RVA=0x{rsrc_rva:x}  size=0x{rsrc_size:x}  file_off=0x{rsrc_off:x}")

# Each ManifestResource has an Offset INTO the resources blob. The first 4
# bytes at that offset are u32 LE size, then the body.
data = pe.__data__  # raw PE bytes

if hasattr(pe.net.mdtables, 'ManifestResource'):
    for mr in pe.net.mdtables.ManifestResource.rows:
        nm = mr.Name.value if mr.Name else ''
        rel_off = mr.struct.Offset
        rsz_off = rsrc_off + rel_off
        size = struct.unpack('<I', data[rsz_off:rsz_off+4])[0]
        body = data[rsz_off+4 : rsz_off+4+size]
        out_path = os.path.join(OUT, nm)
        with open(out_path, 'wb') as f:
            f.write(body)
        print(f"  {nm:60s} {size:>10d} bytes -> {out_path}")
