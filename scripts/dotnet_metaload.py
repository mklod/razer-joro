"""
Use System.Reflection.Metadata.MetadataLoadContext (metadata-only) to walk
CustomerFWU2Point5.exe types/methods without triggering 32/64-bit
assembly loading errors.
"""
import os, sys, glob

import clr
clr.AddReference("System.Reflection.MetadataLoadContext")

from System.Reflection import (
    PathAssemblyResolver,
    MetadataLoadContext,
    BindingFlags,
)
from System.Runtime.InteropServices import RuntimeEnvironment

DEP_DIR = r'L:\PROJECTS\razer-joro\captures\fwu_extract\zip_contents'
PATH = DEP_DIR + r'\CustomerFWU2Point5.exe'

# Resolver: assembly DLLs in the extract dir + .NET runtime DLLs
runtime_dir = RuntimeEnvironment.GetRuntimeDirectory()
core_assemblies = list(glob.glob(os.path.join(runtime_dir, "*.dll")))
print(f"Runtime dir: {runtime_dir}")
print(f"Core assemblies: {len(core_assemblies)}")

# .NET 8+ also needs reference assemblies. Try ref pack for .NET Framework:
# (Sometimes the WindowsDesktop.App ref dir has them.)
extra_dirs = [
    r"C:\Program Files\dotnet\shared\Microsoft.NETCore.App\8.0.26",
    r"C:\Program Files\dotnet\shared\Microsoft.WindowsDesktop.App\8.0.26",
]
for d in extra_dirs:
    if os.path.exists(d):
        core_assemblies.extend(glob.glob(os.path.join(d, "*.dll")))

# Also include the extracted DLLs (they ARE 32-bit but MetadataLoadContext
# doesn't care about bitness)
for d in glob.glob(os.path.join(DEP_DIR, "*.dll")):
    core_assemblies.append(d)
core_assemblies.append(PATH)

resolver = PathAssemblyResolver(core_assemblies)
mlc = MetadataLoadContext(resolver)
asm = mlc.LoadFromAssemblyPath(PATH)

print(f"\nLoaded: {asm.FullName}\n")

# Walk all types
print("=== Types of interest ===")
for t in asm.GetTypes():
    if not t.FullName:
        continue
    name = t.FullName
    if any(k in name.lower() for k in ['enc', 'crypt', 'aes', 'rsa', 'cipher', 'tnc', 'rc4', 'xor', 'firmware']):
        print(f"\n{name}")
        flags = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static | BindingFlags.DeclaredOnly
        for m in t.GetMethods(flags):
            try:
                params = ', '.join(p.ParameterType.Name for p in m.GetParameters())
            except Exception:
                params = '?'
            print(f"  method: {m.ReturnType.Name} {m.Name}({params})")
        for f in t.GetFields(flags):
            try:
                ftype = f.FieldType.Name
            except Exception:
                ftype = '?'
            const_val = ''
            try:
                if f.IsLiteral:
                    cv = f.GetRawConstantValue()
                    const_val = f' = {cv!r}'
            except Exception:
                pass
            print(f"  field: {ftype} {f.Name}{const_val}")
