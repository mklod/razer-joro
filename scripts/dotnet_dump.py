"""
Parse the .NET assembly CustomerFWU2Point5.exe with dnfile.
Extract:
  - Type/method list with focus on encryption/firmware
  - Embedded resources (where the firmware blob may live)
  - User-strings (from #US heap)
"""
import sys
import dnfile

p = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents\CustomerFWU2Point5.exe'
pe = dnfile.dnPE(p)
pe.parse_data_directories()

# Walk types
print("=== TypeDef table (all types in this assembly) ===")
if hasattr(pe.net.mdtables, 'TypeDef'):
    for t in pe.net.mdtables.TypeDef.rows:
        ns = t.TypeNamespace.value if t.TypeNamespace else ''
        nm = t.TypeName.value if t.TypeName else ''
        full = f"{ns}.{nm}" if ns else nm
        if any(k in full.lower() for k in ['enc', 'crypt', 'aes', 'rsa', 'cipher', 'fw', 'firmware', 'joro', 'talia']):
            print(f"  {full}")

print()
print("=== Embedded resources (ManifestResource table) ===")
if hasattr(pe.net.mdtables, 'ManifestResource'):
    for mr in pe.net.mdtables.ManifestResource.rows:
        nm = mr.Name.value if mr.Name else ''
        # Resource size + bytes location
        impl = None
        if mr.Implementation and mr.Implementation.row:
            impl = type(mr.Implementation.row).__name__
        print(f"  name={nm!r}  size={mr.struct.Offset if mr.struct else '?'}  impl={impl}")
        # The resource itself is at the file's resource heap
        try:
            entry = pe.net.resources.entry_offset_from_relative_offset(mr.struct.Offset)
            print(f"    entry_offset={entry}")
        except Exception as e:
            pass

# Read the resource heap manually
print()
print("=== Walk resource heap entries ===")
try:
    rsrc = pe.net.resources.struct
    print(f"  resource heap base size: {pe.net.resources}")
except Exception as e:
    pass

# More direct: read the resources via mdtable + parse
print()
print("=== Method-name search (encryption-related) ===")
if hasattr(pe.net.mdtables, 'MethodDef'):
    for m in pe.net.mdtables.MethodDef.rows:
        nm = m.Name.value if m.Name else ''
        if any(k in nm.lower() for k in ['enc', 'crypt', 'aes', 'rsa', 'cipher', 'decrypt', 'encrypt', 'tnc', 'fw', 'joro']):
            print(f"  {nm}")

# Walk #US strings (user strings) for keys / config
print()
print("=== User-strings containing Joro/Talia/AES key candidates (length 16/24/32) ===")
us = pe.net.user_strings
for tok, s in us.items() if isinstance(us, dict) else []:
    pass

# Direct stream access
mdt = pe.net.metadata
us_stream = mdt.streams.get(b'#US')
if us_stream:
    raw = us_stream.struct
    print(f"  #US stream present")
